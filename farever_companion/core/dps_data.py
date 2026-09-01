"""Pure data model for the DPS engine.

CombatSession / PlayerParse / SkillParse / HitEvent, plus serialization
helpers, weapon-family and class inference, and the module-level constants
the tracker and its consumers rely on.

No process reads, no Qt, no live memory — just data + persistence helpers.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from ..data import names
from ..persist import atomic_write_json
from .damage_events import DamageEvent, K_HEAL, K_SHIELD, K_DAMAGE

log = logging.getLogger(__name__)

PET_PROBE_S = 1.5            # throttle for live owner-slot probes per source addr
PARTY_RETRY_S = 2.0          # cadence for retrying a stale Party_XXXX identity
GROUP_ROSTER_TTL_S = 2.0     # cadence for seeding st.Group roster names

BOSS_KILL_GRACE_S = 5.0     # how long a boss "killed" flag stays credible
BOSS_GONE_IDLE_S = 6.0      # a boss gone this long is considered over

GAME_COMBAT_POLL_S = 0.1          # Hero combat-field read cadence
GAME_COMBAT_END_CONFIRM_S = 1.0   # out-of-combat must hold this long to count
BOSS_IDLE_AUX_S = 2.0             # then a boss fight ends after only this idle

# --- playable classes ---------------------------------------------------#

KNOWN_HERO_CLASSES = ("Warrior", "Rogue", "Mage", "Priest")

# --- owned-item procs (serverSource -> local hero re-credit) ------------#

OWNED_ITEM_PROC_SKILLS = frozenset({
    "Finger of Sukuna'Maatata",
    "Burning Rays",
    "Swarm",
})

# --- weapon families by leading skill-id token --------------------------#

_WEAPON_TOKENS = {
    "sword": "Sword", "greatsword": "Great Sword",
    "dualswords": "Dual Swords", "shield": "Shield",
    "axe": "Axe", "greataxe": "Great Axe", "dualaxes": "Dual Axes",
    "mace": "Mace", "greatmace": "Great Mace", "dualmaces": "Dual Maces",
    "dagger": "Dagger", "daggers": "Daggers", "fists": "Fists",
    "spear": "Spear", "staff": "Staff", "scepter": "Scepter",
    "bow": "Bow", "thrown": "Thrown", "crescent": "Crescent",
    "halos": "Halos", "book": "Book",
}
_CLASS_TOKENS = ("warrior", "rogue", "mage", "priest")


def _weapon_token(skill_id: str) -> str:
    """Leading skill-id token, cleaned for weapon matching."""
    sid = (skill_id or "").strip()
    parts = sid.split("_")
    tok = parts[0].lower()
    if tok in _CLASS_TOKENS and len(parts) > 1:
        tok = parts[1].lower()
    return re.sub(r"\d+$", "", tok)


def weapon_family_of(skill_id: str) -> str | None:
    """Weapon family name (e.g. "Axe") a skill id belongs to, or None.

    Same leading-token rule as weapon_families_used(); pure-cast skills
    (no weapon token) return None so they never show a weapon chip.
    """
    return _WEAPON_TOKENS.get(_weapon_token(skill_id))


def weapon_families_used(session: "CombatSession", player: "PlayerStats | None" = None) -> list[str]:
    """Distinct weapon families a player used this fight."""
    me = player
    if me is None and session:
        for p in session.players.values():
            if p.is_me:
                me = p
                break
        if me is None and session.players:
            me = next(iter(session.players.values()))
    if me is None or not me.skills:
        return []
    fams = set()
    for sid, sp in me.skills.items():
        total_dmg = getattr(sp, "total_damage", getattr(sp, "damage", 0.0))
        heals = getattr(sp, "heals", 0.0)
        hits = getattr(sp, "hit_count", 0)
        heal_cnt = getattr(sp, "heal_count", 0)
        casts = getattr(sp, "cast_count", 0)
        if total_dmg == 0 and heals == 0 and hits == 0 and heal_cnt == 0 and casts == 0:
            continue
        sp_name = getattr(sp, "name", "") or ""
        if sid.endswith("(pet)") or sp_name.endswith("(pet)") or getattr(sp, "is_pet", False):
            continue
        fam = _WEAPON_TOKENS.get(_weapon_token(sid))
        if fam:
            fams.add(fam)
    return sorted(fams)


def weapons_used(session: "CombatSession", player: "PlayerStats | None" = None) -> list[dict]:
    """Distinct weapons a player used this fight, as
    {"name": display name, "type": weapon family} rows — e.g.
    [{"name": "Cheese Moon", "type": "Axe"}].

    Resolved by reverse lookup: each skill the player used is matched
    against the item catalog's weapon skill lists. When a weapon skill
    matches no catalog weapon (e.g. a token whose weapon isn't in the
    bundle), the family name falls back so the loadout line never goes
    empty for a fight that used weapons."""
    me = player
    if me is None and session:
        for p in session.players.values():
            if p.is_me:
                me = p
                break
        if me is None and session.players:
            me = next(iter(session.players.values()))
    if me is None or not me.skills:
        return []
    from ..data.items import weapons_for_skill
    out: list[dict] = []
    seen: set[str] = set()
    for sid, sp in me.skills.items():
        total_dmg = getattr(sp, "total_damage", getattr(sp, "damage", 0.0))
        heals = getattr(sp, "heals", 0.0)
        hits = getattr(sp, "hit_count", 0)
        heal_cnt = getattr(sp, "heal_count", 0)
        casts = getattr(sp, "cast_count", 0)
        if total_dmg == 0 and heals == 0 and hits == 0 and heal_cnt == 0 and casts == 0:
            continue
        sp_name = getattr(sp, "name", "") or ""
        if sid.endswith("(pet)") or sp_name.endswith("(pet)") or getattr(sp, "is_pet", False):
            continue
        ws = weapons_for_skill(sid)
        rows = [{"name": w["name"], "type": w["type"]} for w in ws]
        if not rows:
            fam = _WEAPON_TOKENS.get(_weapon_token(sid))
            if fam:
                rows = [{"name": fam, "type": fam}]
        for r in rows:
            if r["name"] not in seen:
                seen.add(r["name"])
                out.append(r)
    return out


# --- class inference ----------------------------------------------------#

def hero_cls_to_class(h_cls: str, unit_id: str = "") -> str:
    """Canonical class from a hero entity's class or unit_id (matches entity HUD)."""
    from ..data.units import resolve_hero_class
    return resolve_hero_class(h_cls, unit_id)


def infer_class_from_skill(skill_id: str) -> str:
    """Infer player class from their weapon or skill ID."""
    s = (skill_id or "").lower()
    # Class prefix is authoritative; it beats keyword guessing.
    for c in KNOWN_HERO_CLASSES:
        if s.startswith(c.lower() + "_"):
            return c
    if any(k in s for k in ("staff", "fireball", "frost", "arcane", "wizard",
                             "mage", "lightning", "orb")):
        return "Mage"
    if any(k in s for k in ("dagger", "bow", "arrow", "rogue", "assassin",
                             "poison", "shadow")):
        return "Rogue"
    if any(k in s for k in ("tome", "heal", "regen", "priest", "cleric",
                             "blessing", "smite", "holy")):
        return "Priest"
    if any(k in s for k in ("axe", "sword", "shield", "warrior", "fighter",
                             "slash", "spin", "whirlwind", "hammer", "mace")):
        return "Warrior"
    return ""


# --- filesystem helpers -------------------------------------------------#

def _slug_profile(profile: str) -> str:
    """Filesystem-safe slug for a player profile."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", profile or "").strip("._")
    return s or "player"


# --- roster helpers (st.Group) ------------------------------------------#

def _roster_change_key(roster) -> tuple:
    """Stable roster identity for change detection (joins/leaves/swaps change the key)."""
    if roster is None or not getattr(roster, "members", None):
        return ("solo",)
    return tuple(sorted(
        ((mem.name or "").strip(), bool(mem.is_leader))
        for mem in roster.members
        if (mem.name or "").strip()))


def _roster_line(roster) -> str:
    """Human roster line for the Activity Log / stderr."""
    if roster is None or not getattr(roster, "members", None):
        return "solo"
    parts = []
    for mem in roster.members:
        name = (mem.name or "").strip()
        if not name:
            continue
        tags = [t for t in (("leader" if mem.is_leader else None),
                            ("you" if mem.is_me else None)) if t]
        parts.append(f"{name} ({', '.join(tags)})" if tags else name)
    if not parts:
        return "solo"
    return f"{len(parts)} members: " + ", ".join(parts)


# --- data classes ---------------------------------------------------------#

@dataclass
class SkillParse:
    """Per-skill parse over ONE fight segment (damage/heal channels split per event)."""
    skill_id: str
    name: str
    # --- damage channel ---
    damage: float = 0.0
    hit_count: int = 0
    crit_count: int = 0
    min_hit: float = float("inf")
    max_hit: float = 0.0
    # --- heal channel (split into self vs group/ally) ---
    heals: float = 0.0
    self_heals: float = 0.0
    group_heals: float = 0.0
    heal_count: int = 0
    min_heal: float = float("inf")
    max_heal: float = 0.0
    # --- taken (incoming) channel ---
    damage_taken: float = 0.0
    taken_count: int = 0
    last_hit_ts: float = 0.0

    @property
    def total(self) -> float:
        """Both channels summed (what the skill actually did)."""
        return self.damage + self.heals

    def avg_hit(self) -> float:
        return self.damage / max(1, self.hit_count)

    @property
    def crit_pct(self) -> float:
        if self.hit_count <= 0:
            return 0.0
        return (self.crit_count / self.hit_count) * 100.0

    def dps(self, duration: float) -> float:
        return self.damage / max(1.0, duration)

    def share_pct(self, player_total: float) -> float:
        if player_total <= 0:
            return 0.0
        return min(100.0, max(0.0, (self.damage / player_total) * 100.0))

    def heal_share_pct(self, player_heals: float) -> float:
        """Share of the player's healing this skill did."""
        if player_heals <= 0:
            return 0.0
        return min(100.0, max(0.0, (self.heals / player_heals) * 100.0))


@dataclass
class HitEvent:
    timestamp: float
    target_name: str
    target_addr: int
    damage: float
    caster_name: str = "You"
    skill_id: str = "Axe_Base_Attack"
    skill_name: str = "Attack"
    is_crit: bool = False
    is_me: bool = True
    is_heal: bool = False


@dataclass
class PlayerParse:
    name: str
    is_me: bool = True
    hero_class: str = ""           # "Warrior", "Rogue", "Mage", "Priest"
    total_damage: float = 0.0
    damage_taken: float = 0.0
    damage_taken_est: float = 0.0   # portion estimated from hero-HP drops
    heals: float = 0.0
    self_heals: float = 0.0
    group_heals: float = 0.0
    hit_count: int = 0
    heal_count: int = 0
    deaths: int = 0
    last_hit_ts: float = 0.0
    skills: dict[str, SkillParse] = field(default_factory=dict)

    def dps(self, duration: float) -> float:
        eff_dur = max(1.0, duration)
        return self.total_damage / eff_dur

    @property
    def total_damage_taken(self) -> float:
        return self.damage_taken + self.damage_taken_est

    def hps(self, duration: float) -> float:
        eff_dur = max(1.0, duration)
        return self.heals / eff_dur

    def self_hps(self, duration: float) -> float:
        eff_dur = max(1.0, duration)
        return self.self_heals / eff_dur

    def group_hps(self, duration: float) -> float:
        eff_dur = max(1.0, duration)
        return self.group_heals / eff_dur

    def share_pct(self, group_total: float) -> float:
        if group_total <= 0:
            return 0.0
        return min(100.0, max(0.0, (self.total_damage / group_total) * 100.0))

    def heal_share_pct(self, group_heals: float) -> float:
        if group_heals <= 0:
            return 0.0
        return min(100.0, max(0.0, (self.heals / group_heals) * 100.0))

    def ranked_skills(self) -> list[SkillParse]:
        """Skills with an actual damage channel, strongest first."""
        return sorted((s for s in self.skills.values() if s.damage > 0),
                      key=lambda s: s.damage, reverse=True)

    def ranked_heal_skills(self) -> list[SkillParse]:
        """Skills with an actual heal channel, strongest first."""
        return sorted((s for s in self.skills.values() if s.heals > 0),
                      key=lambda s: s.heals, reverse=True)


# --- session serialization -----------------------------------------------#

def _skill_to_dict(sp: SkillParse) -> dict:
    """Clean serialization of a skill parse with rounded numbers."""
    return {
        "skill_id": sp.skill_id,
        "name": sp.name,
        "damage": round(sp.damage, 1),
        "hit_count": sp.hit_count,
        "crit_count": sp.crit_count,
        "min_hit": round(sp.min_hit, 1) if (sp.hit_count > 0 and sp.min_hit != float("inf")) else 0.0,
        "max_hit": round(sp.max_hit, 1),
        "heals": round(sp.heals, 1),
        "self_heals": round(sp.self_heals, 1),
        "group_heals": round(sp.group_heals, 1),
        "heal_count": sp.heal_count,
        "min_heal": round(sp.min_heal, 1) if (sp.heal_count > 0 and sp.min_heal != float("inf")) else 0.0,
        "max_heal": round(sp.max_heal, 1),
        "damage_taken": round(sp.damage_taken, 1),
        "taken_count": sp.taken_count,
        "last_hit_ts": round(sp.last_hit_ts, 2) if sp.last_hit_ts > 0 else 0.0,
    }


def _skill_from_dict(d: dict) -> SkillParse:
    sp = SkillParse(skill_id=d.get("skill_id", ""), name=d.get("name", ""))
    sp.damage = float(d.get("damage", 0.0))
    sp.hit_count = int(d.get("hit_count", 0))
    sp.crit_count = int(d.get("crit_count", 0))
    raw_min = d.get("min_hit")
    sp.min_hit = float(raw_min) if (raw_min is not None and float(raw_min) > 0) else float("inf")
    sp.max_hit = float(d.get("max_hit", 0.0))
    sp.heals = float(d.get("heals", 0.0))
    sp.self_heals = float(d.get("self_heals", 0.0))
    sp.group_heals = float(d.get("group_heals", 0.0))
    # Backward compatibility: if older archive has heals but split is missing, treat as self_heals
    if sp.heals > 0 and sp.self_heals == 0.0 and sp.group_heals == 0.0:
        sp.self_heals = sp.heals
    sp.heal_count = int(d.get("heal_count", 0))
    raw_min_heal = d.get("min_heal")
    sp.min_heal = float(raw_min_heal) if (raw_min_heal is not None and float(raw_min_heal) > 0) else float("inf")
    sp.max_heal = float(d.get("max_heal", 0.0))
    sp.damage_taken = float(d.get("damage_taken", 0.0))
    sp.taken_count = int(d.get("taken_count", 0))
    sp.last_hit_ts = float(d.get("last_hit_ts", 0.0))
    return sp


def _player_to_dict(p: PlayerParse) -> dict:
    """Clean serialization of a player parse with rounded values and active skills only."""
    active_skills = [
        _skill_to_dict(s)
        for s in p.skills.values()
        if (s.damage > 0 or s.heals > 0 or s.damage_taken > 0)
    ]
    return {
        "name": p.name,
        "is_me": p.is_me,
        "hero_class": p.hero_class,
        "total_damage": round(p.total_damage, 1),
        "damage_taken": round(p.damage_taken, 1),
        "damage_taken_est": round(p.damage_taken_est, 1),
        "heals": round(p.heals, 1),
        "self_heals": round(p.self_heals, 1),
        "group_heals": round(p.group_heals, 1),
        "hit_count": p.hit_count,
        "heal_count": p.heal_count,
        "deaths": p.deaths,
        "skills": active_skills,
    }


def _player_from_dict(d: dict) -> PlayerParse:
    p = PlayerParse(name=d.get("name", "?"), is_me=bool(d.get("is_me", False)))
    p.hero_class = d.get("hero_class", "")
    p.total_damage = float(d.get("total_damage", 0.0))
    p.damage_taken = float(d.get("damage_taken", 0.0))
    p.damage_taken_est = float(d.get("damage_taken_est", 0.0))
    p.heals = float(d.get("heals", 0.0))
    p.self_heals = float(d.get("self_heals", 0.0))
    p.group_heals = float(d.get("group_heals", 0.0))
    # Backward compatibility: if older archive has heals but split is missing, treat as self_heals
    if p.heals > 0 and p.self_heals == 0.0 and p.group_heals == 0.0:
        p.self_heals = p.heals
    p.hit_count = int(d.get("hit_count", 0))
    p.heal_count = int(d.get("heal_count", 0))
    p.deaths = int(d.get("deaths", 0))
    for sd in d.get("skills", []):
        sp = _skill_from_dict(sd)
        p.skills[sp.skill_id] = sp
    return p


# --- combat session ------------------------------------------------------#

class CombatSession:
    def __init__(self, name: str = "Overall", kind: str = "overall"):
        self.name: str = name
        self.kind: str = kind  # "overall", "trash", "boss"
        self.state: str = "READY"  # "READY", "COMBAT", "PAUSED"
        self.start_time: float | None = None
        self.end_time: float | None = None
        self.last_event_time: float = 0.0
        self.target_name: str = "None"
        self.target_addr: int = 0
        self.target_hp: float = 0.0
        self.target_max_hp: float = 0.0
        self.players: dict[str, PlayerParse] = {}
        self.hits: deque[HitEvent] = deque(maxlen=60)
        # Timeline plotter data: 1-second damage buckets & death markers
        self.timeline: dict[int, float] = {}             # second -> group damage
        self.player_timeline: dict[str, dict[int, float]] = {}  # player -> second -> damage
        self.deaths: list[tuple[float, str]] = []        # list of (offset_seconds, player_name)
        self.burst_peak: float = 0.0
        self.burst_peak_time: float = 0.0

    def record_death(self, player_name: str):
        now = time.time()
        rel_t = max(0.0, now - (self.start_time or now))
        self.deaths.append((rel_t, player_name))
        if player_name in self.players:
            self.players[player_name].deaths += 1

    @property
    def duration(self) -> float:
        if self.start_time is None:
            return 0.0
        if self.state == "PAUSED" and self.end_time is not None:
            return max(0.0, self.end_time - self.start_time)
        return max(0.0, time.time() - self.start_time)

    @property
    def group_damage(self) -> float:
        return sum(p.total_damage for p in self.players.values())

    @property
    def group_dps(self) -> float:
        dur = self.duration
        if dur <= 0:
            return 0.0
        return self.group_damage / max(1.0, dur)

    @property
    def group_heals(self) -> float:
        return sum(p.heals for p in self.players.values())

    @property
    def group_self_heals(self) -> float:
        return sum(p.self_heals for p in self.players.values())

    @property
    def group_team_heals(self) -> float:
        return sum(p.group_heals for p in self.players.values())

    @property
    def group_hps(self) -> float:
        dur = self.duration
        if dur <= 0:
            return 0.0
        return self.group_heals / max(1.0, dur)

    @property
    def group_taken(self) -> float:
        return sum(p.total_damage_taken for p in self.players.values())

    @property
    def group_taken_est(self) -> float:
        return sum(p.damage_taken_est for p in self.players.values())

    def record_damage_taken(self, name: str, amount: float, is_est: bool = False,
                            is_me: bool = False):
        """Credit incoming damage; ``is_est`` marks HP-drop estimates (≈ in UI)."""
        now = time.time()
        if not is_est and amount > 1.0:
            if self.state == "PAUSED":
                self.resume()
            elif self.state != "COMBAT":
                self.state = "COMBAT"
                if self.start_time is None:
                    self.start_time = now
            self.last_event_time = now
        elif self.state == "COMBAT":
            self.last_event_time = now

        if name not in self.players:
            self.players[name] = PlayerParse(name=name, is_me=is_me)
        p = self.players[name]
        # Estimates stay off the authoritative total (own track for the UI).
        if is_est:
            p.damage_taken_est += amount
        else:
            p.damage_taken += amount

    def register_player(self, name: str, is_me: bool = False, hero_class: str = ""):
        if name not in self.players:
            self.players[name] = PlayerParse(name=name, is_me=is_me, hero_class=hero_class)
        elif hero_class and not self.players[name].hero_class:
            self.players[name].hero_class = hero_class

    def record_hit(self, target_name: str, target_addr: int, target_hp: float,
                   target_max_hp: float, damage: float, caster_name: str = "You",
                   is_me: bool = True, skill_id: str = "Axe_Base_Attack",
                   skill_name: str = "Attack", is_crit: bool = False):
        now = time.time()
        if self.state == "PAUSED":
            self.resume()
        elif self.state != "COMBAT":
            self.state = "COMBAT"
            if self.start_time is None:
                self.start_time = now

        self.last_event_time = now
        if target_name and target_name != "None":
            # Keep the fight's emblem (👑 boss / 🎯 dummy) when a hit arrives
            # with a bare name; without this a dummy label silently lost its
            # 🎯 on the first event tick and flapped back on the next scan.
            emblem = ""
            if self.target_name and not target_name.startswith(("👑 ", "🎯 ")):
                if self.target_name.startswith("👑 "):
                    emblem = "👑 "
                elif self.target_name.startswith("🎯 "):
                    emblem = "🎯 "
            self.target_name = f"{emblem}{target_name}" if emblem else target_name
        if target_addr:
            self.target_addr = target_addr
        if target_hp > 0:
            self.target_hp = target_hp
            self.target_max_hp = max(self.target_max_hp, target_hp)
        if target_max_hp > 0:
            self.target_max_hp = max(self.target_max_hp, target_max_hp, target_hp)

        if caster_name not in self.players:
            self.players[caster_name] = PlayerParse(name=caster_name, is_me=is_me)

        p = self.players[caster_name]
        p.total_damage += damage
        p.hit_count += 1
        p.last_hit_ts = now
        if not p.hero_class:
            p.hero_class = infer_class_from_skill(skill_id)

        if skill_id not in p.skills:
            p.skills[skill_id] = SkillParse(skill_id=skill_id, name=skill_name)
        sp = p.skills[skill_id]
        sp.damage += damage
        sp.hit_count += 1
        sp.min_hit = min(sp.min_hit, damage)
        sp.max_hit = max(sp.max_hit, damage)
        if is_crit:
            sp.crit_count += 1
        sp.last_hit_ts = now

        sec = int(now - (self.start_time or now))
        self.timeline[sec] = self.timeline.get(sec, 0.0) + damage
        if caster_name not in self.player_timeline:
            self.player_timeline[caster_name] = {}
        self.player_timeline[caster_name][sec] = self.player_timeline[caster_name].get(sec, 0.0) + damage
        if self.timeline[sec] > self.burst_peak:
            self.burst_peak = self.timeline[sec]
            self.burst_peak_time = float(sec)

        self.hits.appendleft(
            HitEvent(
                timestamp=now,
                target_name=target_name,
                target_addr=target_addr,
                damage=damage,
                caster_name=caster_name,
                skill_id=skill_id,
                skill_name=skill_name,
                is_crit=is_crit,
                is_me=is_me,
                is_heal=False,
            )
        )

    def record_heal(self, target_name: str, heal_amount: float, caster_name: str = "You",
                    is_me: bool = True, skill_id: str = "Regen", skill_name: str = "Natural Regen"):
        now = time.time()
        if self.state == "PAUSED":
            self.resume()
        elif self.state != "COMBAT":
            self.state = "COMBAT"
            if self.start_time is None:
                self.start_time = now

        self.last_event_time = now
        if caster_name not in self.players:
            self.players[caster_name] = PlayerParse(name=caster_name, is_me=is_me)

        p = self.players[caster_name]
        p.heals += heal_amount
        p.heal_count += 1

        # Determine whether heal is self-targeted vs group/ally
        is_self = (
            target_name == caster_name
            or (is_me and target_name in ("You", caster_name))
            or target_name in ("Self", "You", "")
        )
        if is_self:
            p.self_heals += heal_amount
        else:
            p.group_heals += heal_amount

        if skill_id not in p.skills:
            p.skills[skill_id] = SkillParse(skill_id=skill_id, name=skill_name)
        sp = p.skills[skill_id]
        # Heal channel only; heals never inflate damage totals.
        sp.heals += heal_amount
        if is_self:
            sp.self_heals += heal_amount
        else:
            sp.group_heals += heal_amount
        sp.heal_count += 1
        sp.min_heal = min(sp.min_heal, heal_amount)
        sp.max_heal = max(sp.max_heal, heal_amount)
        sp.last_hit_ts = now

        self.hits.appendleft(
            HitEvent(
                timestamp=now,
                target_name=target_name,
                target_addr=0,
                damage=heal_amount,
                caster_name=caster_name,
                skill_id=skill_id,
                skill_name=skill_name,
                is_crit=False,
                is_me=is_me,
                is_heal=True,
            )
        )

    def pause(self):
        if self.state == "COMBAT":
            self.state = "PAUSED"
            self.end_time = self.last_event_time or time.time()

    def check_combat_lull(self, now: float, timeout_s: float = 6.0) -> bool:
        """Auto-pause session if in combat but idle for timeout_s seconds."""
        if self.state == "COMBAT" and self.last_event_time:
            if now - self.last_event_time >= timeout_s:
                self.pause()
                return True
        return False

    def resume(self):
        if self.state == "PAUSED":
            self.state = "COMBAT"
            if self.start_time and self.end_time:
                paused_len = time.time() - self.end_time
                self.start_time += max(0.0, paused_len)
            self.end_time = None

    def reset(self):
        self.state = "READY"
        self.start_time = None
        self.end_time = None
        self.last_event_time = 0.0
        self.target_name = "None"
        self.target_addr = 0
        self.target_hp = 0.0
        self.target_max_hp = 0.0
        self.players.clear()
        self.hits.clear()
        self.timeline.clear()
        self.player_timeline.clear()
        self.deaths.clear()
        self.burst_peak = 0.0
        self.burst_peak_time = 0.0

    def ranked_players(self, view: str = "damage", pin_me: bool = True,
                       include_empty_allies: bool = False) -> list[PlayerParse]:
        if view in ("healing", "heals"):
            natural = sorted(self.players.values(), key=lambda p: p.heals, reverse=True)
        elif view in ("taken", "damage_taken"):
            natural = sorted(self.players.values(), key=lambda p: p.total_damage_taken, reverse=True)
        else:
            natural = sorted(self.players.values(), key=lambda p: p.total_damage, reverse=True)

        if not include_empty_allies:
            natural = [p for p in natural
                       if p.is_me or p.total_damage > 0 or p.heals > 0 or p.total_damage_taken > 0]

        if pin_me and natural:
            me_list = [p for p in natural if p.is_me]
            others = [p for p in natural if not p.is_me]
            if me_list:
                return me_list + others

        return natural

    def get_rank(self, player: PlayerParse, view: str = "damage",
                 include_empty_allies: bool = False) -> int:
        if view in ("healing", "heals"):
            natural = sorted(self.players.values(), key=lambda p: p.heals, reverse=True)
        elif view in ("taken", "damage_taken"):
            natural = sorted(self.players.values(), key=lambda p: p.total_damage_taken, reverse=True)
        else:
            natural = sorted(self.players.values(), key=lambda p: p.total_damage, reverse=True)
        if not include_empty_allies:
            natural = [p for p in natural
                       if p.is_me or p.total_damage > 0 or p.heals > 0 or p.total_damage_taken > 0]
        try:
            return natural.index(player) + 1
        except ValueError:
            return 1


def _session_to_dict(s: CombatSession) -> dict:
    """Archived CombatSession -> clean, rounded JSON-able dict without junk."""
    active_players = []
    for p in s.players.values():
        # Exclude temporary Party_XXXX placeholders with no stats
        if p.name.startswith("Party_") and p.total_damage <= 0 and p.heals <= 0:
            continue
        # Exclude bystander entities with no activity unless local player
        if not p.is_me and p.total_damage <= 0 and p.heals <= 0 and p.total_damage_taken <= 0:
            continue
        active_players.append(_player_to_dict(p))

    dur = round(s.duration, 1)
    grp_dmg = round(s.group_damage, 1)
    grp_heals = round(s.group_heals, 1)

    return {
        "name": s.name,
        "kind": s.kind,
        "start_time": round(s.start_time, 2) if s.start_time is not None else None,
        "end_time": round(s.end_time, 2) if s.end_time is not None else None,
        "duration": dur,
        "state": s.state,
        "target_name": s.target_name,
        "target_addr": s.target_addr,
        "target_hp": round(s.target_hp, 1),
        "target_max_hp": round(s.target_max_hp, 1),
        "group_damage": grp_dmg,
        "group_heals": grp_heals,
        "group_dps": round(s.group_dps, 1),
        "group_hps": round(s.group_hps, 1),
        "players": active_players,
        "timeline": {str(k): round(v, 1) for k, v in s.timeline.items() if round(v, 1) > 0},
        "deaths": [[round(float(t), 1), str(n)] for t, n in s.deaths],
        "burst_peak": round(s.burst_peak, 1),
        "burst_peak_time": round(s.burst_peak_time, 1),
    }


def _session_from_dict(d: dict) -> CombatSession:
    """Rebuild an archived CombatSession (state PAUSED so duration() is stored length)."""
    s = CombatSession(name=d.get("name", "Fight"), kind=d.get("kind", "overall"))
    s.state = "PAUSED"
    s.start_time = float(d["start_time"]) if d.get("start_time") is not None else None
    s.end_time = float(d["end_time"]) if d.get("end_time") is not None else None
    # ``target`` is the legacy key; accept both so old archives keep their name.
    s.target_name = d.get("target_name") or d.get("target") or "None"
    s.target_addr = int(d.get("target_addr", 0))
    s.target_hp = float(d.get("target_hp", 0.0))
    s.target_max_hp = float(d.get("target_max_hp", 0.0))
    for pd in d.get("players", []):
        p = _player_from_dict(pd)
        s.players[p.name] = p
    s.timeline = {int(k): float(v) for k, v in d.get("timeline", {}).items()}
    s.deaths = [[float(t), n] for t, n in d.get("deaths", [])]
    s.burst_peak = float(d.get("burst_peak", 0.0))
    s.burst_peak_time = float(d.get("burst_peak_time", 0.0))
    return s


def read_history_file(path: str | Path) -> list[CombatSession]:
    """Load archived fights from one history file; corrupt/missing -> []."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    loaded = []
    for d in (data.get("history") or []):
        try:
            loaded.append(_session_from_dict(d))
        except Exception:
            continue
    return loaded
