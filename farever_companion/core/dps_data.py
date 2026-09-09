"""Pure data model for the DPS engine.

CombatSession / PlayerParse / SkillParse / HitEvent, plus serialization
helpers, weapon-family and class inference, and the module-level constants
the tracker and its consumers rely on.

No process reads, no Qt, no live memory — just data + persistence helpers.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from ..data import names
from .damage_events import (  # noqa: F401  (re-exported: dps_tracker_events imports K_* through here)
    DamageEvent,
    K_HEAL,
    K_SHIELD,
    K_DAMAGE,
    PET_SUFFIX as _PET_SUFFIX,
)

log = logging.getLogger(__name__)

PET_PROBE_S = 1.5            # throttle for live owner-slot probes per source addr
PARTY_RETRY_S = 2.0          # cadence for retrying a stale Party_XXXX identity
GROUP_ROSTER_TTL_S = 2.0     # cadence for seeding st.Group roster names

# A boss that vanishes from the unit scan for less than this long is treated
# as scan flicker (rift arenas can carry a second boss-tagged entity, and a
# boss can drop out of the scan for a single poll). The meter keeps its pinned
# target instead of hopping between boss entities every tick; only after the
# pinned boss has stayed gone past this window does the tracker re-target.
BOSS_RELATCH_GRACE_S = 1.5
INSTANCE_EDGE_DEBOUNCE_S = 2.0  # is_dungeon edge must hold this long before
                                # the zone-in auto-reset wipes the meter

BOSS_KILL_GRACE_S = 5.0     # how long a boss "killed" flag stays credible
BOSS_GONE_IDLE_S = 6.0      # a boss gone this long is considered over
# Damage lull against a boss that is STILL ALIVE: the pull is over even though
# the boss never died and never despawned (walked off, died, dropped to adds).
# Much longer than the old 6 s trash lull on purpose - the gap between boss
# hits is genuinely long (rotations, repositioning, an untargetable phase), so
# a trash-length lull would cut real boss fights in half. (That trash lull is
# dormant: the tick no longer runs `check_combat_lull`, out-of-combat only
# seals a pack - see `dps_tracker_tick._auto_pause_lull`.)
BOSS_DAMAGE_LULL_S = 20.0

GAME_COMBAT_POLL_S = 0.1          # Hero combat-field read cadence
FIGHT_ANCHOR_MAX_S = 120.0        # a game combat-start older than this is
#                                   unrelated to the hit that starts the fight
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


def weapon_families_used(session: "CombatSession", player: "PlayerParse | None" = None) -> list[str]:
    """Distinct weapon families a player used this fight."""
    me = player
    if me is None and session:
        me = own_row(session)
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


def weapon_for_skill(skill_id: str, equipped: dict[str, dict] | None = None) -> dict | None:
    """The one weapon row a skill id belongs to, or None.

    Reverse lookup against the item catalog's weapon skill lists: a
    signature skill that maps to exactly one weapon resolves to that
    weapon's real name (e.g. Beefury's Swarm Cleave). A skill the catalog
    does not list (status, proc, unlisted active) or one shared by a whole
    family resolves to the family, refined by the LIVE gear walk to the
    equipped weapon of that family when the reader knows the loadout
    ("Axe" -> "Cheese Moon"). A pure-cast skill — no weapon token — is
    None, never a fabricated weapon.
    """
    from ..data.items import weapons_for_skill
    equipped = equipped or {}
    ws = weapons_for_skill(skill_id)
    if len(ws) == 1:
        return {"name": ws[0]["name"], "type": ws[0]["type"]}
    fam = _WEAPON_TOKENS.get(_weapon_token(skill_id))
    if not fam:
        return None
    row = equipped.get(fam)
    if row is not None:
        return {"name": row["name"], "type": fam}
    return {"name": fam, "type": fam}


def _skill_did_something(sp) -> bool:
    """True when a skill row carries any real activity (not a zeroed stub)."""
    return not (getattr(sp, "total_damage", getattr(sp, "damage", 0.0)) == 0
                and getattr(sp, "heals", 0.0) == 0
                and getattr(sp, "hit_count", 0) == 0
                and getattr(sp, "heal_count", 0) == 0
                and getattr(sp, "cast_count", 0) == 0)


def _is_pet_skill(sid: str, sp) -> bool:
    sp_name = getattr(sp, "name", "") or ""
    return bool(sid.endswith("(pet)") or sp_name.endswith("(pet)")
                or getattr(sp, "is_pet", False))


def _skill_channel_value(sp, metric: str) -> float:
    """What a skill row is ranked by for a page metric."""
    if metric == "healing":
        return getattr(sp, "heals", 0.0)
    if metric == "skills":
        return getattr(sp, "total", getattr(sp, "damage", 0.0))
    return getattr(sp, "damage", 0.0)


def top_skill_weapon(session: "CombatSession", player: "PlayerParse | None" = None,
                     equipped: dict[str, dict] | None = None,
                     metric: str = "damage") -> dict | None:
    """The weapon behind the player's single strongest skill this fight.

    The loadout line answers "which weapon did this come from", and the
    answer is the weapon the biggest number came from — a two-weapon
    loadout's slot 1 is not necessarily the weapon that actually did the
    damage. Skills are ranked by the metric the page is showing (damage /
    heals / both channels) and the first one that resolves to a real
    weapon wins; a player whose top skill is a pure cast walks down the
    ranking until a weapon-backed skill answers. None when nothing
    resolves.
    """
    me = player
    if me is None and session:
        me = own_row(session)
        if me is None and session.players:
            me = next(iter(session.players.values()))
    if me is None or not me.skills:
        return None
    ranked = sorted((s for s in me.skills.values() if _skill_did_something(s)),
                    key=lambda s: _skill_channel_value(s, metric), reverse=True)
    for sp in ranked:
        sid = getattr(sp, "skill_id", "") or ""
        if not sid or _is_pet_skill(sid, sp):
            continue
        row = weapon_for_skill(sid, equipped=equipped)
        if row is not None:
            return row
    return None


def weapons_used(session: "CombatSession", player: "PlayerParse | None" = None,
                 equipped: dict[str, dict] | None = None) -> list[dict]:
    """Distinct weapons a player used this fight, as
    {"name": display name, "type": weapon family} rows — e.g.
    [{"name": "Cheese Moon", "type": "Axe"}].

    One `weapon_for_skill` resolution per used skill, in the session's own
    skill order, de-duplicated by display name."""
    me = player
    if me is None and session:
        me = own_row(session)
        if me is None and session.players:
            me = next(iter(session.players.values()))
    if me is None or not me.skills:
        return []
    equipped = equipped or {}
    out: list[dict] = []
    seen: set[str] = set()
    for sid, sp in me.skills.items():
        if not _skill_did_something(sp):
            continue
        if _is_pet_skill(sid, sp):
            continue
        row = weapon_for_skill(sid, equipped=equipped)
        if row is None:
            continue
        if row["name"] not in seen:
            seen.add(row["name"])
            out.append(row)
    return out


# --- class inference ----------------------------------------------------#

def hero_cls_to_class(h_cls: str, unit_id: str = "") -> str:
    """Canonical class from a hero entity's class or unit_id (matches entity HUD).

    Only the game's known player classes (Warrior/Rogue/Mage/Priest) resolve;
    any other ``ent.hero.X`` leaf (e.g. Druid, a non-player class) returns ""
    so class inference never fabricates a class the game doesn't support."""
    from ..data.units import resolve_hero_class
    cls = resolve_hero_class(h_cls, unit_id)
    if cls and cls in KNOWN_HERO_CLASSES:
        return cls
    return ""


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
    """Human roster line for the Activity Log / stderr.

    "Could not read the group" and "there is nobody in the group" are different
    facts, and this used to print "solo" for both. That matters because
    `solo_status` treats an unreadable roster as UNKNOWN and declines to filter,
    while this line asserted the opposite - so a group object the reader could
    not decode (a rift's pre-formed party is a new shape to find) produced a log
    saying "solo" and a filter quietly keeping the honest answer. A reader has
    to be able to trust that the log is not making a claim.
    """
    if roster is None:
        return "roster unavailable (group object not decoded - not a claim of solo)"
    if not getattr(roster, "members", None):
        return "solo (group object decoded, no members)"
    parts = []
    for mem in roster.members:
        name = (mem.name or "").strip()
        if not name:
            continue
        tags = [t for t in (("leader" if mem.is_leader else None),
                            ("you" if mem.is_me else None)) if t]
        parts.append(f"{name} ({', '.join(tags)})" if tags else name)
    if not parts:
        return "solo (group object decoded, no members)"
    n = len(parts)
    return f"{n} member{'' if n == 1 else 's'}: " + ", ".join(parts)


def own_row(session):
    """The local player's row in a session, or None before they land a hit.

    This expression was written out seven times - here, in `view_totals`, the
    Top DPS overlay, the Combat page, the rail (twice) and the roster - so
    every surface that answers "which row is mine" had its own copy of the
    answer. That is not a style complaint: when the bridge flagged EVERY hero
    as `isMe` (a #define reading a pointer's low byte), all seven copies
    independently picked the same wrong row, and the meter, the page, the rail
    and the roster agreed on nonsense together. One owner, so one bug.

    Duck-typed on purpose - `getattr` rather than attribute access - so a test
    double session without `is_me` on a row is a row that is not mine, not a
    raised AttributeError halfway through a tick.
    """
    for p in (getattr(session, "players", None) or {}).values():
        if getattr(p, "is_me", False):
            return p
    return None


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
    # --- damage-by-affinity channel (the game's own per-hit damage type) ---
    # Only tagged hits land here, so the buckets can sum to LESS than
    # ``damage`` (untagged hits, or a bridge older than the field). Consumers
    # must show the remainder rather than silently normalizing it away.
    affinity_damage: dict[str, float] = field(default_factory=dict)
    affinity_hits: dict[str, int] = field(default_factory=dict)
    # Damage that arrived under a DERIVED row of this skill and was folded in
    # here rather than getting its own row: child id -> that child's OWN parse.
    # The bleed a Bonethrow applies, the ticks a passive's status deals.
    #
    # Stored as full parses, not bare damage, because the meter renders these
    # as real nested rows and a row needs hits, crits and avg - not just a
    # number to add up. The parent's own numbers ALREADY include these (they
    # were added above), so `subskills` is a breakdown of the parent, never an
    # addition to it.
    subskills: dict[str, "SkillParse"] = field(default_factory=dict)

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
        return self.damage_taken

    def hps(self, duration: float) -> float:
        eff_dur = max(1.0, duration)
        return self.heals / eff_dur


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

#: Keys a serializer must never drop even when they hold the reader's default,
#: because losing them is NOT the same as storing their zero: a session with no
#: `start_time` key reads back as "no clock", which is a different fight from
#: one that started at 0.0.
_ALWAYS_KEEP = frozenset({"name", "kind", "start_time", "end_time", "skill_id"})


def _without_defaults(d: dict) -> dict:
    """Drop every field whose value already IS the reader's default for it.

    Most of an archived row is fields that only apply to SOME kinds of entry.
    A pure-damage skill wrote `heals`, `self_heals`, `group_heals`,
    `heal_count`, `min_heal`, `max_heal`, `damage_taken`, `taken_count` and
    `affinity_hits` as zeroes on every one of its rows, and the players and
    sessions carried the same padding (live 2026-10-04: "my dps logs is now in
    mbs ... we dont need rows where the value is 0"). Every one of those keys is
    read back with a matching `.get(key, default)` in `_skill_from_dict` /
    `_player_from_dict` / `_session_from_dict`, so a missing key and a stored
    zero load identically — the file gets smaller and the fight it describes
    does not change. This is also why the archives stay forward-compatible: a
    reader that predates a field already had to tolerate its absence.

    `_ALWAYS_KEEP` guards the handful of keys whose absence means something
    else (see its comment).
    """
    return {k: v for k, v in d.items()
            if k in _ALWAYS_KEEP or v not in (False, 0, 0.0, "", [], {})}


def _skill_to_dict(sp: SkillParse) -> dict:
    """Clean serialization of a skill parse with rounded numbers.

    Zero-valued fields are omitted (see `_without_defaults`): a skill averages
    a dozen keys and typically uses half of them, so this is the bulk of the
    saving on a season of logs.
    """
    return _without_defaults({
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
        "affinity_damage": {a: round(v, 1) for a, v in sp.affinity_damage.items()},
        "affinity_hits": dict(sp.affinity_hits),
        # The folded sub-skills ride along, or an ARCHIVED fight loses the
        # breakdown the live meter shows: the parent row's number stayed whole
        # while the explanation for 250 of it disappeared, so DPS Analysis and
        # Past Fights read differently from the meter on the same fight.
        # Absent on archives written before the field existed -> empty.
        "subskills": {cid: _skill_to_dict(child)
                      for cid, child in sp.subskills.items()},
        "last_hit_ts": round(sp.last_hit_ts, 2) if sp.last_hit_ts > 0 else 0.0,
    })


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
    # Absent on archives written before the field existed -> empty, not an error.
    sp.affinity_damage = {str(a): float(v) for a, v in (d.get("affinity_damage") or {}).items()}
    sp.affinity_hits = {str(a): int(v) for a, v in (d.get("affinity_hits") or {}).items()}
    # Folded sub-skills, each its own parse (see _skill_to_dict). Older
    # archives have no such key -> empty, never an error: the parent's damage
    # is already whole, so the row stays correct and only the breakdown is
    # missing.
    sp.subskills = {str(cid): _skill_from_dict(child)
                    for cid, child in (d.get("subskills") or {}).items()}
    sp.last_hit_ts = float(d.get("last_hit_ts", 0.0))
    return sp


def _player_to_dict(p: PlayerParse) -> dict:
    """Clean serialization of a player parse with rounded values and active skills only."""
    active_skills = [
        _skill_to_dict(s)
        for s in p.skills.values()
        if (s.damage > 0 or s.heals > 0 or s.damage_taken > 0)
    ]
    # Zero-valued fields are omitted (see `_without_defaults`): a full-healer
    # row keeps its heal keys and drops the damage ones, and vice versa.
    return _without_defaults({
        "name": p.name,
        "is_me": p.is_me,
        "hero_class": p.hero_class,
        "total_damage": round(p.total_damage, 1),
        "damage_taken": round(p.damage_taken, 1),
        "heals": round(p.heals, 1),
        "self_heals": round(p.self_heals, 1),
        "group_heals": round(p.group_heals, 1),
        "hit_count": p.hit_count,
        "heal_count": p.heal_count,
        "deaths": p.deaths,
        "skills": active_skills,
    })


def _player_from_dict(d: dict) -> PlayerParse:
    p = PlayerParse(name=d.get("name", "?"), is_me=bool(d.get("is_me", False)))
    p.hero_class = d.get("hero_class", "")
    p.total_damage = float(d.get("total_damage", 0.0))
    p.damage_taken = float(d.get("damage_taken", 0.0))
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

@dataclass
class TargetParse:
    """The group's damage bucketed by the target it was dealt to.

    Born from the training-dummy cluster: several dummies standing together are
    deliberately ONE target group for the tracker (they share the dummy
    session, which is what makes the group total right), but a total alone
    cannot answer "which dummy took what" — one cleave or AoE dumps into the
    same number, so a test against the armour dummy reads exactly like one
    against the magic-resist dummy. This is that split, filled from the same
    `record_hit` that feeds the players (the dummy group only), so the rows can
    never drift from the session total they add up to.

    Keyed by target address when the event carries one, else by the target's
    own name: a bridge-injected hit has no address, and two dummies sharing a
    display name must still stay apart (their addresses differ), while a
    name-keyed hit must not be folded into an address-keyed row it may not
    belong to.
    """
    addr: int = 0
    name: str = "Enemy"
    damage: float = 0.0
    hits: int = 0
    # Last live HP for THIS target. The scene scan owns it (a hit refreshes it
    # too), so every row's bar is that dummy's own health rather than the pinned
    # target's. 0 = never read, which a row must not draw a bar out of.
    hp: float = 0.0
    max_hp: float = 0.0

    @property
    def key(self) -> int | str:
        """This row's key in `CombatSession.targets`: its address when the hit
        carried one, else its label (see `CombatSession.record_hit`). It is what
        a surface hands back when the player clicks the row."""
        return self.addr or self.name

    @property
    def has_hp(self) -> bool:
        """Whether a health bar can be drawn for this target yet."""
        return self.max_hp > 0.0

    @property
    def hp_pct(self) -> float:
        """HP as a percentage, 0.0 while the target's max is unknown."""
        if self.max_hp <= 0.0:
            return 0.0
        return max(0.0, min(100.0, (self.hp / self.max_hp) * 100.0))

    def share_pct(self, total: float) -> float:
        """This target's share of the split total (`total <= 0` counts as 0)."""
        if total <= 0:
            return 0.0
        return (self.damage / total) * 100.0

    def dps(self, duration: float) -> float:
        return self.damage / max(1.0, duration)


@dataclass
class PackSplit:
    """One mob pack's damage, bucketed by the mob it was dealt to.

    A trash session spans a whole dungeon: every pack from the first pull to
    the boss is one `CombatSession`, so a single target split across it cannot
    say which pull a mob died in or how a pack's damage compares with the next
    one's. That is the question trash is actually asked ("did that pack go
    down fast, or did we spend the whole pull on one caster"), and a group
    total cannot answer it however it is sliced.

    So the pack, not the mob, is the row here: one of these per pull, each
    holding its own `TargetParse` rows keyed exactly as `CombatSession.targets`
    keys them (address when the hit carried one, else the label). Pack 1 is
    the first pull, so a reader can order them by index without parsing the
    names, and `damage` sums to the session total whenever every hit carried a
    target — the same identity `CombatSession.target_total` relies on.
    """
    index: int = 1
    start_time: float = 0.0
    end_time: float = 0.0
    targets: dict[int | str, TargetParse] = field(default_factory=dict)

    @property
    def key(self) -> int:
        """This pack's row key in `CombatSession.packs`: its 1-based index."""
        return self.index

    @property
    def damage(self) -> float:
        """Damage across this pack's own rows (see `CombatSession.target_total`)."""
        return sum(t.damage for t in self.targets.values())

    @property
    def duration(self) -> float:
        return max(0.0, self.end_time - self.start_time)

    def ranked_targets(self) -> list[TargetParse]:
        """This pack's mobs, biggest damage first."""
        return sorted(self.targets.values(), key=lambda t: t.damage, reverse=True)

    @property
    def mob_count(self) -> int:
        """Distinct mobs this pack held, by the same address-or-name key."""
        return len(self.targets)


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
        # A dummy test's header target can be pinned BY HAND (a clicked split
        # row) — see `select_target`. While it is locked the automatic rules
        # (the damage leader, then the nearest dummy) stand down, which is the
        # whole point: you click the dummy you care about and go on hitting a
        # different one.
        self.target_locked: bool = False
        self.players: dict[str, PlayerParse] = {}
        # Per-target split (address-or-name -> TargetParse): the group's damage
        # bucketed by what it was dealt to. Filled for the DUMMY group only —
        # several dummies standing together are one session by design, so a
        # split is the only way to see which of them took the damage. Left
        # empty on every other kind on purpose: recording it everywhere would
        # hand the boss/overall boards a boss + adds + trash split that no
        # surface shows.
        self.targets: dict[int | str, TargetParse] = {}
        # The BOSS's own row: how much it hit the party for, on which skills,
        # and how much it took (its damage taken is `target_total`, the other
        # side of the fight, so it is not tracked twice). Deliberately NOT in
        # `players`: `group_damage`, `group_taken`, `ranked_players` and every
        # existing board read that dict, and a boss competing for the top of
        # the damage table is not a DPS contributor — the raid's damage race is
        # between the players. The boss is the thing they are racing.
        self.boss_row: PlayerParse | None = None
        # Whether this fight was WON. Set by the tracker at the moment a boss's
        # HP reaches zero, so it is a fact about the fight rather than a string
        # left to be parsed back out of the archive's name. A personal best has
        # to mean fastest KILL: folding a wipe into the record would make the
        # number it reports a lie, because a pull abandoned at 10% is the
        # shortest run anyone has ever had against that boss.
        self.boss_killed: bool = False
        # The boss this session belongs to, once the tracker knows it. Used for
        # FILE ROUTING only (`DpsTrackerHistory.file_for`): a run against a
        # known boss is written beside that boss's other runs rather than into
        # the day-wide file, so a folder reads as one boss and its packs.
        self.boss_label: str = ""
        # The instance DIFFICULTY the game itself reported for this fight
        # ("normal" / "hard" / "heroic"), or "" when it could not be read.
        # Never the manual fallback: it is written into the log's FILE NAME,
        # where a guess would be permanent (see `game_state.instance_mode`).
        self.mode: str = ""
        # The TRASH pack breakdown (see PackSplit): one entry per pull, each
        # with its own per-mob rows. A trash session covers a whole dungeon,
        # so its flat `targets` above cannot say which pull a mob died in.
        # Populated for the trash group only, and only across real pack
        # boundaries (a lull or the game's out-of-combat edge, which pause
        # this session and open the next pack) - a single continuous fight is
        # one pack, not one pack per hit.
        self.packs: list[PackSplit] = []
        # The pack currently taking hits, or None when no pull is open.
        self._pack_open: PackSplit | None = None
        # Set by `record_hit` when the hit that arrived RESUMED a paused trash
        # session, i.e. it belongs to the next pull rather than the open one.
        self._pack_resume: bool = False
        self.hits: deque[HitEvent] = deque(maxlen=60)
        # Timeline plotter data: 1-second damage buckets & death markers
        self.timeline: dict[int, float] = {}             # second -> group damage
        self.player_timeline: dict[str, dict[int, float]] = {}  # player -> second -> damage
        self.deaths: list[tuple[float, str]] = []        # list of (offset_seconds, player_name)
        self.burst_peak: float = 0.0
        self.burst_peak_time: float = 0.0
        # Wall time the GAME says this fight began (0 = unknown), kept in sync
        # by the tracker from the Hero combat fields. Used only when the clock
        # first starts: the first decoded hit is always at/after the real
        # start, so anchoring to the game's own combat entry keeps capture
        # latency (a blind re-anchor, a throttled tick) out of the timer.
        self.clock_anchor: float = 0.0

    def _fight_start(self, now: float) -> float:
        """Start the clock at combat entry when the game tells us when that was.

        ``now`` is the timestamp of the first decoded hit. Anchoring to the
        game's combat start is what makes the timer start when the fight
        starts instead of when the DAMAGE finally decodes (live 2026-09-19:
        Option 3 could be blind for the first seconds of a pull, so the timer
        started late by exactly that much). A missing, future or implausibly
        old anchor loses to the event time.
        """
        a = self.clock_anchor
        if 0.0 < a <= now and (now - a) <= FIGHT_ANCHOR_MAX_S:
            return a
        return now

    def record_death(self, player_name: str):
        now = time.time()
        rel_t = max(0.0, now - (self.start_time or now))
        self.deaths.append((rel_t, player_name))
        if player_name in self.players:
            self.players[player_name].deaths += 1

    @property
    def duration(self) -> float:
        """The fight's span in EVENT time - the denominator for every rate.

        While the fight is live this used to be measured on the wall clock
        (`time.time() - start_time`). That is a different clock from the one
        the archive uses, and the fight's own bookkeeping already leans on the
        event clock: `pause()` stamps `end_time` from the last event (up to a
        lull timeout early), and `resume()` shifts `start_time` by the gap
        between two event times. So the paused interval was only half-removed
        from a live span, and none of it was removed from the archived one:
        live DPS read low and then jumped when the fight was filed. Live
        2026-10-03, a rift boss showed 600 on the meter and 637 in DPS
        Analysis - ~6%, and only on fights containing a pause.

        Anchoring a live session's "now" to its last event makes the two
        identical by construction, and costs nothing else: every rate in the
        app (DPS, HPS, damage taken, timeline axes) wants the same span the
        archive will use. The ticking wall-clock timer is a separate
        measurement now - see `wall_duration` - because a fight timer has to
        keep moving between hits.
        """
        if self.start_time is None:
            return 0.0
        if self.state == "PAUSED" and self.end_time is not None:
            return max(0.0, self.end_time - self.start_time)
        # `>=`, not `>`. The two stamps are EQUAL whenever the clock was just
        # re-opened by the hit that resumed it: `resume()` shifts `start_time`
        # forward to exactly this event's stamp, and `record_hit` then stores
        # that same stamp as `last_event_time`. With a strict `>` that equality
        # fell through to the WALL clock, which is read at DRAIN time - so a
        # backlog delivered late (the live "DPS drain stalled 20s ... events
        # drained late") leaked the whole drain delay into the rate
        # denominator. Measured: a fight resumed 5 s in, drained 25 s later,
        # reported `duration` 25.0 s instead of 0.0 s, i.e. every rate on that
        # board read ~1/26th of the truth. The event-anchored span is 0.0 here
        # and that is correct - the clock has only just re-opened.
        if self.last_event_time >= self.start_time:
            return max(0.0, self.last_event_time - self.start_time)
        return max(0.0, time.time() - self.start_time)

    @property
    def wall_duration(self) -> float:
        """The fight's span on the wall clock, for the ticking timer.

        Deliberately NOT `duration`: this one is expected to advance with no
        input at all, which is the entire point of a run timer. It is
        display-only - nothing computes a rate from it, or the 2026-10-03
        live/archived split would simply move here.
        """
        if self.start_time is None:
            return 0.0
        if self.state == "PAUSED" and self.end_time is not None:
            return max(0.0, self.end_time - self.start_time)
        return max(0.0, time.time() - self.start_time)

    @property
    def group_damage(self) -> float:
        return sum(p.total_damage for p in self.players.values())

    @property
    def target_total(self) -> float:
        """Damage across the per-target split (see TargetParse).

        Equals `group_damage` whenever every hit carried a target, which is the
        normal case; the split total is what the rows add up to, so the HUD
        shows this number beside them rather than a group total that could
        disagree with them.
        """
        return sum(t.damage for t in self.targets.values())

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
    def group_hps(self) -> float:
        dur = self.duration
        if dur <= 0:
            return 0.0
        return self.group_heals / max(1.0, dur)

    @property
    def group_taken(self) -> float:
        return sum(p.total_damage_taken for p in self.players.values())

    def record_damage_taken(self, name: str, amount: float,
                            is_me: bool = False, when: float = 0.0):
        """Credit incoming damage from a real damage event.

        ``when`` is the event's capture timestamp (DamageEvent.t): honoring it
        keeps a backlog drained late (timer throttling, UI stall) from
        rewriting the fight's clock — live 2026-09-19, a boss pull drained in
        one gulp archived as a 0.0 s fight because every record call re-stamped
        the wall clock at drain time.
        """
        now = when if when > 0.0 else time.time()
        if amount > 1.0:
            if self.state == "PAUSED":
                self.resume(now)
            elif self.state != "COMBAT":
                self.state = "COMBAT"
                if self.start_time is None:
                    self.start_time = self._fight_start(now)
            self.last_event_time = now
        elif self.state == "COMBAT":
            self.last_event_time = now

        if name not in self.players:
            self.players[name] = PlayerParse(name=name, is_me=is_me)
        p = self.players[name]
        p.damage_taken += amount

    def register_player(self, name: str, is_me: bool = False, hero_class: str = ""):
        if name not in self.players:
            self.players[name] = PlayerParse(name=name, is_me=is_me, hero_class=hero_class)
        elif hero_class and not self.players[name].hero_class:
            self.players[name].hero_class = hero_class

    def _emblem(self) -> str:
        """The group's own emblem (the boss crown / dummy target) taken from the
        session label, "" when it carries none. Every hit's target is spelled
        with it, so one rule covers the board header and the split rows."""
        name = self.target_name or ""
        for mark in ("👑 ", "🎯 "):
            if name.startswith(mark):
                return mark
        return ""

    def _follows_pinned(self, target_addr: int) -> bool:
        """Whether a hit on `target_addr` may move this session's pinned target.

        Always yes for the group that follows what it hits: a trash session is
        meant to track whatever is being fought. The boss and dummy groups are
        PINNED instead - "the pinned target, or a hit whose target cannot be
        identified at all" (a bridge event carries no address) - and the pin
        moves BY RULE, never because a hit happened to name a different unit:
        the engage sets it, the tick's `_track_boss_target` re-latches it to a
        respawned/phase-swapped boss, and `damage_leader` / `select_target`
        move it for a dummy cluster.

        The dummy reason was one AoE landing on three dummies per swing, which
        made the board's name and bar hop several times a second. The boss
        reason is worse than a hop: an encounter's adds are named after the
        boss they belong to (`Nepsilon_Totem` is a Nepsilon pull's totem), so
        an add's hit re-pointed the session at the add - its death then
        confirmed a BOSS kill, the fight ended early and the archive was named
        after the add (live 2026-10-02: a 10-minute boss run filed as a run of
        short "Boss Fight" rows plus `Tidal Totem_<profile>.json`).
        """
        if self.kind not in ("dummy", "boss"):
            return True
        if not target_addr or not self.target_addr:
            return True
        return int(target_addr) == int(self.target_addr)

    # --- the header target's pin (dummy group) ---------------------------#

    def pinned_bucket(self) -> TargetParse | None:
        """The split row the header is currently describing, if it is one."""
        key = self.target_addr or self.target_name
        if not key or key == "None":
            return None
        return self.targets.get(key)

    def pin_bucket(self, tgt: TargetParse) -> bool:
        """Point the session's header (name, bar) at split row `tgt`.

        The row's own live HP comes with it so the top bar shows the right
        dummy on the very next paint rather than a tick later, when the scene
        scan catches up. Returns True when the header actually moved.
        """
        before = (self.target_name, self.target_addr)
        self.target_addr = tgt.addr
        self.target_name = tgt.name
        if tgt.has_hp:
            self.target_hp = tgt.hp
            self.target_max_hp = tgt.max_hp
        return before != (self.target_name, self.target_addr)

    def select_target(self, key: int | str) -> bool:
        """Make split row `key` the header target until the test ends.

        A selection is a LOCK, not a hint: the whole reason to click a row is to
        keep watching that dummy while you go on hitting a different one, so the
        damage-leader rule stands down until `clear_target_selection` (or a
        second click on the same row) hands the pin back.

        Returns True when something moved — including the second click, which
        releases the lock and lets the automatic rule choose again.
        """
        if self.kind != "dummy":
            return False
        tgt = self.targets.get(key)
        if tgt is None:
            return False
        if self.target_locked and self.pinned_bucket() is tgt:
            self.target_locked = False
            return True
        self.target_locked = True
        return self.pin_bucket(tgt)

    def clear_target_selection(self) -> None:
        """Hand the pin back to the automatic rule (the damage leader)."""
        self.target_locked = False

    def damage_leader(self) -> TargetParse | None:
        """The split row that has taken the most damage, or None to keep the pin.

        This is the automatic rule the dummy test's header follows, and it is
        what makes the pin useful: "the one that took the most damage" is the
        answer to "which dummy am I actually testing", and it cannot be
        computed by the player at a glance across three identical rows.

        Only a STRICT improvement over the current pin counts, deliberately. An
        AoE that credits three dummies for the same amount several times a
        second would otherwise walk the header bar across the yard, which is
        exactly the hopping this replaced.
        """
        if not self.targets:
            return None
        best = max(self.targets.values(), key=lambda t: t.damage)
        if best.damage <= 0.0:
            return None
        pinned = self.pinned_bucket()
        if pinned is not None and best.damage <= pinned.damage:
            return None
        return best

    def note_target(self, target_name: str, target_addr: int, damage: float,
                    target_hp: float = 0.0,
                    target_max_hp: float = 0.0) -> TargetParse | None:
        """Bucket one damaging hit into the per-target split. Returns the row.

        Split out of `record_hit` because a fight's split does not always
        belong to the session the hit feeds. The BOSS fight is two sessions —
        the boss's damage goes to `boss_session`, an add's to
        `boss_adds_session` — but "how much of the boss board went to the boss
        versus its adds" is ONE question with one answer, so the tracker calls
        this once per hit on `boss_session` for the whole fight and the rows
        sum to the fight total instead of scattering across two sessions.

        Keyed exactly as the dummy cluster's split is: by target address when
        the hit carries one, else by the label, so two adds sharing a display
        name still stay apart while a name-keyed hit cannot be folded into a
        row it may not belong to.
        """
        label = target_name or "Enemy"
        tkey = target_addr or label
        tgt = self.targets.get(tkey)
        if tgt is None:
            tgt = TargetParse(addr=target_addr or 0, name=label)
            self.targets[tkey] = tgt
        elif label and tgt.name != label:
            # A later, better-resolved spelling wins over the one the first
            # hit carried (the same reason the skill rows re-apply labels).
            tgt.name = label
        tgt.damage += damage
        tgt.hits += 1
        if target_hp > 0.0:
            tgt.hp = target_hp
        if target_max_hp > 0.0:
            tgt.max_hp = max(tgt.max_hp, target_max_hp)
        # The header follows the DAMAGE LEADER: which target has actually taken
        # the most is the question, and it is the only answer a cluster of three
        # identical rows can give at a glance. Applied at the hit that changed
        # the ranking — not on the next tick, which would put the pinned bar a
        # tick behind the number it is pinned to. A clicked row outranks it
        # (`select_target`), and only a STRICT improvement counts, so an AoE
        # crediting the whole cluster equally cannot walk the pin around.
        if not self.target_locked:
            leader = self.damage_leader()
            if leader is not None:
                self.pin_bucket(leader)
        return tgt

    def begin_pack(self, when: float = 0.0) -> PackSplit | None:
        """Open the next trash pack and make it the one taking hits.

        Called at a real pack boundary — the tracker pauses this session when
        the game leaves combat or the damage lull expires, and the next hit
        resumes it, so that resume is where one pull ends and the next begins.
        Opening a pack on anything else would make a single long fight read as
        a hundred packs, which is the opposite of the split being asked for.

        A pack already open is left alone (the boundary signals can arrive in
        bursts: the lull pause and the out-of-combat pause can both fire
        within one tick of each other, and a pack must not be split by that).
        Returns the open pack, or None if the session is not a trash group.
        """
        if self.kind != "trash":
            return None
        if self._pack_open is not None:
            return self._pack_open
        now = when if when > 0.0 else time.time()
        pack = PackSplit(index=len(self.packs) + 1, start_time=now, end_time=now)
        self.packs.append(pack)
        self._pack_open = pack
        return pack

    def close_pack(self, when: float = 0.0) -> None:
        """Seal the open pack so the next hit opens a fresh one.

        Called where a trash pull is known to be over (the fight lulled out,
        the boss engaged, the meter was reset). A zero-damage pack is dropped
        rather than archived: a boundary that opened a pack and then closed it
        with nothing in it is a lull the player never actually fought through,
        and an empty row in a breakdown is a question with no answer behind it.
        """
        pack = self._pack_open
        self._pack_open = None
        if pack is None:
            return
        if when > 0.0:
            pack.end_time = when
        if not pack.targets and self.packs and self.packs[-1] is pack:
            self.packs.pop()

    def note_pack_target(self, target_name: str, target_addr: int, damage: float,
                         target_hp: float = 0.0, target_max_hp: float = 0.0,
                         when: float = 0.0) -> TargetParse | None:
        """Bucket one trash hit into the open pack. Returns the row.

        Opens a pack if none is open, so the first hit of a pull always lands
        somewhere instead of being dropped for want of a boundary, and stamps
        it from THIS hit's own capture timestamp — never the wall clock, for
        the same reason `resume` refuses it: a backlog drained late must not
        date a pack to the moment it was decoded, or its span collapses to
        nothing and its hits land before the pull they belong to.

        Keyed by address when the hit carries one, else by the label — the same
        rule `note_target` uses, so two mobs sharing a display name stay apart
        while a name-keyed hit cannot be folded into a row it may not belong
        to. Damage only: a heal's target is an ally, not a mob in the pack.
        """
        if damage <= 0.0:
            return None
        pack = self._pack_open or self.begin_pack(when)
        if pack is None:
            return None
        label = target_name or "Enemy"
        tkey = target_addr or label
        tgt = pack.targets.get(tkey)
        if tgt is None:
            tgt = TargetParse(addr=target_addr or 0, name=label)
            pack.targets[tkey] = tgt
        elif label and tgt.name != label:
            # A later, better-resolved spelling wins over the one the first
            # hit carried (same reason as `note_target`).
            tgt.name = label
        tgt.damage += damage
        tgt.hits += 1
        if target_hp > 0.0:
            tgt.hp = target_hp
        if target_max_hp > 0.0:
            tgt.max_hp = max(tgt.max_hp, target_max_hp)
        return tgt

    def note_boss_damage(self, skill_id: str, skill_name: str, damage: float,
                         is_crit: bool = False, affinity: str = "") -> None:
        """Credit one hit the BOSS landed on the party to its own row.

        The outgoing path only ever sees damage dealt TO a foe, so everything a
        boss does to the party arrives here instead, on the incoming branch
        (`record_damage_taken` knows who was hit but not who hit them). Without
        this the boss was a hole in the record: a fight's logs said what the
        party did and nothing about what it cost them.

        Credited to `boss_row`, never to `players` (see the field's note), so no
        group total, ranking or existing board moves. `damage_taken` is left
        alone — it is filled from `target_total` at read time, which is the
        same fight counted from the other side and cannot disagree with itself.
        """
        if damage <= 0.0:
            return
        if self.boss_row is None:
            self.boss_row = PlayerParse(name=self._boss_row_name(), is_me=False)
        row = self.boss_row
        row.total_damage += damage
        row.hit_count += 1
        if not row.name or row.name in ("Boss", "None"):
            row.name = self._boss_row_name()
        if not skill_id:
            skill_id = "Attack"
        if skill_id not in row.skills:
            row.skills[skill_id] = SkillParse(skill_id=skill_id,
                                              name=skill_name or skill_id)
        sp = row.skills[skill_id]
        if skill_name and sp.name != skill_name:
            sp.name = skill_name
        sp.damage += damage
        sp.hit_count += 1
        if is_crit:
            sp.crit_count += 1

    def _boss_row_name(self) -> str:
        """The boss's row name: the meter's label without its crown.

        `target_name` carries the 👑 the board draws so the eye finds the fight
        (see `record_hit`'s label graft). That is right on a board and wrong in
        a row that has to sit beside player names and be sorted among them, so
        the emblem comes off here and only here.
        """
        name = (self.target_name or "").replace("\U0001f451", "").strip()
        return name if name and name != "None" else "Boss"

    @property
    def boss_damage_taken(self) -> float:
        """What the party dealt the boss — the other side of `boss_row`.

        Read from the per-target split rather than accumulated separately, so
        the boss's damage and the damage it took are two readings of ONE set of
        hits and can never drift apart.
        """
        return self.target_total


    def record_hit(self, target_name: str, target_addr: int, target_hp: float,
                   target_max_hp: float, damage: float, caster_name: str = "You",
                   is_me: bool = True, skill_id: str = "Axe_Base_Attack",
                   skill_name: str = "Attack", is_crit: bool = False,
                   when: float = 0.0, affinity: str = ""):
        now = when if when > 0.0 else time.time()
        # A trash pull boundary is this session's own pause: the tracker pauses
        # it when the game leaves combat or the damage lull expires, and the
        # next hit resumes it. Remember that resume so the pack split below
        # opens a fresh pack for the new pull instead of folding it into the
        # last one. Tracked on the session rather than in the tick because both
        # observe the same pause - one boundary, one flag.
        if self.kind == "trash":
            self._pack_resume = (self.state == "PAUSED")
        if self.state == "PAUSED":
            self.resume(now)
        elif self.state != "COMBAT":
            self.state = "COMBAT"
            if self.start_time is None:
                self.start_time = self._fight_start(now)

        self.last_event_time = now
        # This hit's own target label, spelled the way the board spells its
        # group (boss crown / dummy target). Without the graft a dummy label
        # silently lost its emblem on the first event tick and flapped back on
        # the next scan.
        label = target_name
        if (target_name and target_name != "None"
                and not target_name.startswith(("👑 ", "🎯 "))):
            label = "%s%s" % (self._emblem(), target_name)

        # Who this hit may move: the pinned target only (see _follows_pinned).
        # The per-target split below is where the other dummies' numbers go.
        follows = self._follows_pinned(target_addr)
        if target_name and target_name != "None" and follows:
            self.target_name = label
        if target_addr and follows:
            self.target_addr = target_addr

        # --- per-target split (see TargetParse) ---------------------------
        # The hit that just fed the players also feeds the bucket for whatever
        # it landed on, for the dummy group only (see `targets` above). Damage
        # only: a heal's target is an ally, not a parse target.
        if self.kind == "dummy" and damage > 0.0:
            self.note_target(target_name=label, target_addr=target_addr,
                             damage=damage, target_hp=target_hp,
                             target_max_hp=target_max_hp)
        # --- trash pack split (see PackSplit) -----------------------------
        # The same hit, bucketed into the pack currently taking damage. Fed
        # from here rather than from the tracker because the PACK boundary is
        # this session's own pause: this hit RESUMED a paused session (the lull
        # or the game's out-of-combat edge ended the last pull), so it belongs
        # to the next pack, not the open one. Sealing and reopening on the
        # resume is what makes the split self-contained — the tick's own
        # `close_pack` calls only stamp the boundary at its true time and seal
        # the last pack before an archive copies the session, so the two can
        # never disagree about where a pack ended.
        if self.kind == "trash" and damage > 0.0:
            if self._pack_resume:
                self._pack_resume = False
                self.close_pack(now)
                self.begin_pack(now)
            self.note_pack_target(target_name=label, target_addr=target_addr,
                                  damage=damage, target_hp=target_hp,
                                  target_max_hp=target_max_hp, when=now)
            if self._pack_open is not None:
                self._pack_open.end_time = now
        if target_hp > 0 and follows:
            self.target_hp = target_hp
            self.target_max_hp = max(self.target_max_hp, target_hp)
        if target_max_hp > 0 and follows:
            self.target_max_hp = max(self.target_max_hp, target_max_hp, target_hp)

        if caster_name not in self.players:
            self.players[caster_name] = PlayerParse(name=caster_name, is_me=is_me)

        p = self.players[caster_name]
        p.total_damage += damage
        p.hit_count += 1
        p.last_hit_ts = now
        if not p.hero_class:
            p.hero_class = infer_class_from_skill(skill_id)

        folded = None
        if skill_id not in p.skills:
            # A derived row of a skill this player has ALREADY used folds
            # into that skill rather than taking a row of its own. Both
            # carry the same display name - `Axe_Boomerang_Skill1` and
            # `Axe_Boomerang_Skill1_Status` are BOTH "Bonethrow" in the
            # sheet - so the meter showed two identical rows and the bleed
            # read as a second skill (reported live 2026-10-03).
            #
            # Gated on the parent already being in the parse, which is what
            # keeps this narrow: a status from a source the player never cast
            # stays its own row rather than being attributed to whatever
            # skill happens to share its prefix.
            parent = names.parent_skill_id(skill_id)
            if parent and parent != skill_id and parent in p.skills:
                folded = skill_id
                skill_id = parent
        # The child's own parse is kept BEFORE the fold, so a nested row has
        # real hit counts and crits rather than a bare damage total.
        child_sp = None
        if folded:
            child_sp = p.skills.get(folded)
            if child_sp is None:
                child_sp = SkillParse(skill_id=folded, name=skill_name)
                p.skills[folded] = child_sp
        if skill_id not in p.skills:
            p.skills[skill_id] = SkillParse(skill_id=skill_id, name=skill_name)
        sp = p.skills[skill_id]
        sp.damage += damage
        sp.hit_count += 1
        sp.min_hit = min(sp.min_hit, damage)
        sp.max_hit = max(sp.max_hit, damage)
        if folded and child_sp is not None:
            child_sp.damage += damage
            child_sp.hit_count += 1
            child_sp.min_hit = min(child_sp.min_hit, damage)
            child_sp.max_hit = max(child_sp.max_hit, damage)
            if is_crit:
                child_sp.crit_count += 1
            if affinity:
                child_sp.affinity_damage[affinity] =                     child_sp.affinity_damage.get(affinity, 0.0) + damage
                child_sp.affinity_hits[affinity] =                     child_sp.affinity_hits.get(affinity, 0) + 1
            child_sp.last_hit_ts = now
            sp.subskills[folded] = child_sp
            # The child is folded, not counted twice: it must not keep a slot
            # in the player's own skill list, or it reappears as a row of its
            # own the moment this fold is bypassed.
            p.skills.pop(folded, None)
        if is_crit:
            sp.crit_count += 1
        if affinity:
            sp.affinity_damage[affinity] = sp.affinity_damage.get(affinity, 0.0) + damage
            sp.affinity_hits[affinity] = sp.affinity_hits.get(affinity, 0) + 1
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
                    is_me: bool = True, skill_id: str = "Regen", skill_name: str = "Natural Regen",
                    when: float = 0.0):
        now = when if when > 0.0 else time.time()
        if self.state == "PAUSED":
            self.resume(now)
        elif self.state != "COMBAT":
            self.state = "COMBAT"
            if self.start_time is None:
                self.start_time = self._fight_start(now)

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
            # Same fold as the damage channel: a derived row of a skill
            # already in the parse belongs to that skill's row, heal or not.
            parent = names.parent_skill_id(skill_id)
            if parent and parent != skill_id and parent in p.skills:
                skill_id = parent
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

    def resume(self, when: float = 0.0):
        """Re-open a paused fight, shifting its clock past the pause.

        ``when`` is the resuming event's capture timestamp and MUST come from
        it, never from the wall clock. A backlog drained late (throttled
        timer, capture re-anchor, UI stall) calls this with an already-old
        timestamp; shifting `start_time` by the WALL-CLOCK gap instead walks
        the fight's clock forward on every pause/resume cycle until the
        duration collapses. Live 2026-09-19: a 6,342-damage boss kill was
        archived as `start_time=...624.83, end_time=...624.84` -- a 0.01 s
        fight, which the Top DPS header renders as a timer that starts and
        then shows 00:00.0. An event that predates the pause (when <=
        end_time, i.e. a re-delivered one) shifts nothing.
        """
        if self.state == "PAUSED":
            self.state = "COMBAT"
            now = when if when > 0.0 else time.time()
            if self.start_time and self.end_time:
                paused_len = now - self.end_time
                self.start_time += max(0.0, paused_len)
            self.end_time = None

    def reset(self):
        self.state = "READY"
        self.start_time = None
        self.end_time = None
        self.last_event_time = 0.0
        # `clock_anchor` is deliberately NOT cleared here: the tracker owns it
        # from the game's combat fields, and it re-applies it every tick. A
        # reset mid-fight (engagement reset, manual reset) must not lose the
        # game's fight-start, or the first hit of the very same tick would
        # start the clock late again.
        self.target_name = "None"
        self.target_addr = 0
        self.target_hp = 0.0
        self.target_max_hp = 0.0
        self.target_locked = False
        self.players.clear()
        self.boss_row = None
        self.boss_killed = False
        self.targets.clear()
        self.packs.clear()
        self._pack_open = None
        self._pack_resume = False
        self.hits.clear()
        self.timeline.clear()
        self.player_timeline.clear()
        self.deaths.clear()
        self.burst_peak = 0.0
        self.burst_peak_time = 0.0

    def keeps_bystanders(self) -> bool:
        """Whether damage TAKEN alone earns a player a row in this session.

        A dummy does not fight back, so damage taken during a dummy test is
        the world or the player's own rotation — not another player taking
        part. A teammate who ate a stray hit while standing around must not
        enter the dummy parse (live 2026-10-02: a dummy session listed four
        idle party members, each with only chips of damage taken). Every
        other kind keeps the taken-inclusive rule, because an instance
        pre-registers the party between pulls on purpose so the overlay can
        auto-load them.
        """
        return self.kind != "dummy"

    def is_active_player(self, p: PlayerParse) -> bool:
        """Whether `p` earned a row: you, a real contribution, or (off a
        dummy) damage taken. Chip damage taken alone is not a dummy parse."""
        if getattr(p, "is_me", False):
            return True
        if p.total_damage > 0 or p.heals > 0:
            return True
        return (self.keeps_bystanders()
                and getattr(p, "total_damage_taken", 0.0) > 0)

    def active_players(self) -> list[PlayerParse]:
        """Every player who earned a row, in no particular order."""
        return [p for p in self.players.values() if self.is_active_player(p)]

    def ranked_targets(self) -> list[TargetParse]:
        """The per-target split, biggest damage first (see TargetParse)."""
        return sorted(self.targets.values(),
                      key=lambda t: t.damage, reverse=True)

    def ranked_players(self, view: str = "damage", pin_me: bool = True,
                       include_empty_allies: bool = False) -> list[PlayerParse]:
        if view in ("healing", "heals"):
            natural = sorted(self.players.values(), key=lambda p: p.heals, reverse=True)
        elif view in ("taken", "damage_taken"):
            natural = sorted(self.players.values(), key=lambda p: p.total_damage_taken, reverse=True)
        else:
            natural = sorted(self.players.values(), key=lambda p: p.total_damage, reverse=True)

        if not include_empty_allies:
            natural = [p for p in natural if self.is_active_player(p)]

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
            natural = [p for p in natural if self.is_active_player(p)]
        try:
            return natural.index(player) + 1
        except ValueError:
            return 1


def _target_row_to_dict(t: TargetParse) -> dict:
    """One per-mob split row -> JSON. Shared by the boss split and the pack
    breakdown so both serialize a mob identically (see `PackSplit`)."""
    return {
        "name": t.name,
        "addr": int(t.addr),
        "damage": round(t.damage, 1),
        "hits": int(t.hits),
        "hp": round(t.hp, 1),
        "max_hp": round(t.max_hp, 1),
    }


def _target_row_from_dict(td: dict) -> TargetParse:
    """One archived per-mob split row -> TargetParse, keyed as it was written."""
    t = TargetParse(addr=int(td.get("addr", 0)),
                    name=td.get("name") or "Enemy",
                    damage=float(td.get("damage", 0.0)),
                    hits=int(td.get("hits", 0)),
                    hp=float(td.get("hp", 0.0)),
                    max_hp=float(td.get("max_hp", 0.0)))
    return t


def _session_to_dict(s: CombatSession) -> dict:
    """Archived CombatSession -> clean, rounded JSON-able dict without junk."""
    active_players = []
    for p in s.players.values():
        # Exclude temporary Party_XXXX placeholders with no stats
        if p.name.startswith("Party_") and p.total_damage <= 0 and p.heals <= 0:
            continue
        # Exclude bystander entities with no activity unless local player. A
        # dummy session additionally drops chip-damage-only rows (see
        # CombatSession.keeps_bystanders), so an idle teammate never reaches
        # the saved file either.
        if not s.is_active_player(p):
            continue
        active_players.append(_player_to_dict(p))

    # The per-target split, BOSS FIGHTS ONLY. A boss fight buckets the boss and
    # every add into one list (see `note_target`), so this is the only record of
    # how the board was actually spent once the fight is over - the archived
    # segments answer "who did what", this answers "onto what". Emitted only
    # when there is one, so every other kind's file keeps its exact old shape.
    targets = []
    if s.kind == "boss":
        for t in s.ranked_targets():
            targets.append(_target_row_to_dict(t))

    # The PACK breakdown, TRASH ONLY (see PackSplit). A trash archive covers a
    # whole dungeon's pulls in one session, so this is the only record of which
    # pull a mob died in and how the packs compared - the flat player rows say
    # who did what, this says onto which group of mobs and when. Emitted only
    # when there is one, so every other kind's file keeps its exact old shape.
    packs = []
    if s.kind == "trash":
        for p in s.packs:
            if not p.targets:
                continue
            packs.append({
                "index": int(p.index),
                "start_time": round(p.start_time, 2),
                "end_time": round(p.end_time, 2),
                "damage": round(p.damage, 1),
                "mobs": int(p.mob_count),
                "targets": [_target_row_to_dict(t) for t in p.ranked_targets()],
            })

    out = {
        "name": s.name,
        "kind": s.kind,
        "start_time": round(s.start_time, 2) if s.start_time is not None else None,
        "end_time": round(s.end_time, 2) if s.end_time is not None else None,
        # ``duration``, ``state`` and the four ``group_*`` totals are written
        # nowhere: an archived session loads as PAUSED and computes all six
        # from its own rows on read (see `_session_from_dict`), so storing them
        # only inflated every file. Sized and verified by
        # `test_a_session_does_not_write_the_totals_it_can_recompute`.
        "target_name": s.target_name,
        "target_addr": s.target_addr,
        "target_hp": round(s.target_hp, 1),
        "target_max_hp": round(s.target_max_hp, 1),
        "players": active_players,
        "timeline": {str(k): round(v, 1) for k, v in s.timeline.items() if round(v, 1) > 0},
        "deaths": [[round(float(t), 1), str(n)] for t, n in s.deaths],
        "burst_peak": round(s.burst_peak, 1),
        "burst_peak_time": round(s.burst_peak_time, 1),
    }
    if targets:
        out["targets"] = targets
    if packs:
        out["packs"] = packs
    # The boss's own row (see `note_boss_damage`), kept OUT of `players` on
    # purpose so no existing board moves. Boss fights only, and only when one
    # was actually recorded, so every other kind's file keeps its old shape.
    if s.kind == "boss" and s.boss_row is not None and s.boss_row.total_damage > 0:
        boss_row = _player_to_dict(s.boss_row)
        boss_row["damage_taken"] = round(s.boss_damage_taken, 1)
        out["boss_row"] = boss_row
    if s.boss_label:
        out["boss_label"] = s.boss_label
    # Only when the game actually named one: an archive with no `mode` key is
    # an unknown difficulty, which is not the same claim as "normal".
    if getattr(s, "mode", ""):
        out["mode"] = s.mode
    if s.kind == "boss" and s.boss_killed:
        out["boss_killed"] = True
    # The same zero-field trim as the skill and player rows: an empty timeline,
    # deaths list or players list is the reader's default, and a session with
    # nothing taken keeps none of the taken keys.
    return _without_defaults(out)


def _session_from_dict(d: dict) -> CombatSession:
    """Rebuild an archived CombatSession (state PAUSED so duration() is stored length)."""
    s = CombatSession(name=d.get("name", "Fight"), kind=d.get("kind", "overall"))
    s.state = "PAUSED"
    s.start_time = float(d["start_time"]) if d.get("start_time") is not None else None
    s.end_time = float(d["end_time"]) if d.get("end_time") is not None else None
    # ``target`` is the legacy key; accept both so old archives keep their name.
    s.target_name = d.get("target_name") or d.get("target") or "None"
    s.boss_label = d.get("boss_label") or ""
    # Anything that is not one of the three difficulties (a hand-edited file, a
    # future value) loads as unknown rather than as a difficulty we would then
    # show a badge for.
    _mode = d.get("mode")
    s.mode = _mode.lower() if isinstance(_mode, str) \
        and _mode.lower() in ("normal", "hard", "heroic") else ""
    # Absent on every archive written before kills were flagged; False is the
    # right reading of those either way — an old run's fate is unknown, and
    # guessing it was a kill would put a wipe into a personal best.
    s.boss_killed = bool(d.get("boss_killed"))
    s.target_addr = int(d.get("target_addr", 0))
    s.target_hp = float(d.get("target_hp", 0.0))
    s.target_max_hp = float(d.get("target_max_hp", 0.0))
    for pd in d.get("players", []):
        p = _player_from_dict(pd)
        s.players[p.name] = p
    # The boss's own row (see `note_boss_damage`). Deliberately NOT merged into
    # `players` on the way back in either: an archive's player rows are what the
    # boards rank, and folding a boss into them on load would make a reloaded
    # fight disagree with the live one it came from.
    if d.get("boss_row"):
        s.boss_row = _player_from_dict(d["boss_row"])
    s.timeline = {int(k): float(v) for k, v in d.get("timeline", {}).items()}
    s.deaths = [[float(t), n] for t, n in d.get("deaths", [])]
    s.burst_peak = float(d.get("burst_peak", 0.0))
    s.burst_peak_time = float(d.get("burst_peak_time", 0.0))
    # The boss fight's per-target split (see `note_target`). An archive written
    # before this key existed simply has none, which is the same as a fight
    # with one target.
    for td in d.get("targets") or []:
        t = _target_row_from_dict(td)
        s.targets[t.key] = t
    # The trash pack breakdown (see `PackSplit`). Old archives have no `packs`
    # key at all, which loads as an empty list - a trash run recorded before
    # this existed simply has no pull breakdown, and nothing else changes.
    for pd in d.get("packs") or []:
        pack = PackSplit(index=int(pd.get("index", len(s.packs) + 1) or 1),
                         start_time=float(pd.get("start_time", 0.0)),
                         end_time=float(pd.get("end_time", 0.0)))
        for td in pd.get("targets") or []:
            t = _target_row_from_dict(td)
            pack.targets[t.key] = t
        if pack.targets:
            s.packs.append(pack)
    # Every pack read back is left SEALED — no open pack — because an archived
    # fight is over. A hit landing on a loaded session therefore opens a NEW
    # pack rather than silently inflating the run's final one: an archive on
    # disk must be a record of what happened, never a number that grows after
    # the fact because the meter was pointed back at it.
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
