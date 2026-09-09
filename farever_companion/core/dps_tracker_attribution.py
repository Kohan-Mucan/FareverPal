"""Attribution helpers shared by the DPS tracker surfaces.

This module contains process-free name/roster decisions only. Capture remains
isolated in ``dps_bridge.py`` and ``dps_memory.py``.
"""
from __future__ import annotations

from functools import lru_cache

from ..data import names
from .dps_data import own_row


def merge_player_into(dst, src) -> None:
    """Merge a placeholder player's totals and skills into a resolved row."""
    dst.total_damage += src.total_damage
    dst.damage_taken += src.damage_taken
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
        # The affinity breakdown must fold too, or resolving a Party_XXXX
        # placeholder silently drops that player's damage-by-type.
        for aff, dealt in ssp.affinity_damage.items():
            dsp.affinity_damage[aff] = dsp.affinity_damage.get(aff, 0.0) + dealt
        for aff, hits in ssp.affinity_hits.items():
            dsp.affinity_hits[aff] = dsp.affinity_hits.get(aff, 0) + hits
        dsp.last_hit_ts = max(dsp.last_hit_ts, ssp.last_hit_ts)


_GENERIC_PLAYER_LABELS = frozenset({"Player", "Hero", "You", "Unknown"})


def same_player_name(left: str, right: str) -> bool:
    """Compare player names while ignoring UI decoration and case.

    The local row is displayed as ``Name (You)`` while bridge and roster
    payloads normally contain bare ``Name``. Server character names are
    unique, so a normalized exact match is stronger evidence than a drifted
    entity/player pointer.
    """
    def _key(value: str) -> str:
        key = " ".join(str(value or "").split()).casefold()
        suffix = " (you)"
        return key[:-len(suffix)] if key.endswith(suffix) else key

    a, b = _key(left), _key(right)
    return bool(a and b and a == b)


@lru_cache(maxsize=8192)
def _canonical_unit_display(name: str) -> str:
    """Return the display spelling for a raw unit id."""
    if not name:
        return name
    stripped = name.replace("👑 ", "").replace("🎯 ", "").strip()
    if not stripped or " " in stripped or "/" in stripped or "\\" in stripped:
        return name
    resolved = names.unit_name(stripped)
    return resolved if resolved != stripped else name


def _read_roster(model):
    """Return the current roster, or ``None`` when it cannot be decoded."""
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
    """Return whether the model is inside a dungeon or rift instance."""
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
    """Return true only when a decoded roster confirms the player is solo."""
    if model is None:
        return None
    pa = getattr(model, "player_addr", 0)
    if not pa and not getattr(model, "is_attached", False):
        return None
    if _in_instance(model):
        return False
    roster = _read_roster(model)
    if roster is None:
        return None
    members = list(getattr(roster, "members", []) or [])
    return not any(not getattr(member, "is_me", False) for member in members)


def solo_only_view(model, settings=None, tracker=None) -> bool:
    """Return whether the self-only view should be active."""
    if solo_status(model, tracker) is not True:
        return False
    return bool(getattr(settings, "dps_solo_only", True))


def view_totals(session, solo_only: bool = False) -> tuple[float, float, float]:
    """Return damage, healing, and taken totals for a display surface."""
    if solo_only:
        me = own_row(session)
        if me is not None:
            return (me.total_damage, me.heals, me.total_damage_taken)
    return (session.group_damage, session.group_heals, session.group_taken)


# Tracker-bound attribution operations. Keeping these beside the pure name and
# roster helpers makes pet ownership and placeholder folding independently
# testable without touching either capture engine.
from .dps_data import OWNED_ITEM_PROC_SKILLS, PET_PROBE_S


class DpsTrackerAttribution:
    def __init__(self, tracker):
        self.tracker = tracker

    def _fold_party_row(self, old_name: str, owner_name: str,
                        pet_skills: bool = False) -> None:
        self = self.tracker
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
                merge_player_into(owner, old)
            pt = sess.player_timeline
            bucket = pt.pop(old_name, None)
            if bucket:
                ob = pt.setdefault(owner_name, {})
                for sec, amt in bucket.items():
                    ob[sec] = ob.get(sec, 0.0) + amt

    def _map_pet_source(self, src: int, pa: int,
                        heroes: dict[int, tuple[str, bool, tuple[float, float, float], str]],
                        now: float) -> tuple[bool, int]:
        self = self.tracker
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
        self = self.tracker
        """Re-credit an owned-item proc source to the local hero (``pa`` or 0)."""
        if not src or not pa or src == pa:
            return 0
        if src in self._owned_proc_sources:
            return self._owned_proc_sources[src]
        name = names.skill_name(skill_raw or "") or skill_raw or ""
        if name not in OWNED_ITEM_PROC_SKILLS:
            return 0
        # Refuse any source we have ALREADY identified as a player — including
        # an ally. The old check only skipped a source cached as `is_me`, so a
        # scanned party member whose hero address was known (and who may hold
        # the same item) had their proc ticks re-credited onto the local
        # player. A placeholder (Party_XXXX) still falls through: unidentified
        # is exactly the case this re-credit exists to resolve.
        if self._is_resolved_player(src):
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
