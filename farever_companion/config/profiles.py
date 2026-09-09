"""The per-profile accessors Settings inherits.

Split out of the dataclass body: the fields and core persistence stay in
`model`, while everything that reads or writes one profile's state (POI done,
hidden units, dps/speedrun bests, owned gear) lives here as a plain mixin - it
adds no dataclass field, so field order is unchanged.
"""

from __future__ import annotations

import json

from ..runtime.persist import atomic_write_json, backup_corrupt

from .store import (
    _categorize_companion,
    _collection_path,
    _read_collection_disk,
    config_dir,
    load_speedrun_history,
    write_speedrun_history,
)


def _clean_bests(best) -> dict:
    """`best` without the sub-second entries the 00:00 bug recorded.

    The floor lives in core.speedrun (MIN_PLAUSIBLE_SPLIT_S); the import is
    deferred to keep config core-free, and on failure the dict passes through
    untouched rather than losing a record.
    """
    try:
        from ..core.speedrun import clean_best_dict
        return clean_best_dict(best)[0]
    except Exception:
        return dict(best or {})


class _ProfileAccessors:
    # --- profile data helpers -------------------------------------------
    def _ensure_profile_loaded(self, profile: str) -> None:
        if profile not in self._profile_progress:
            path = config_dir() / f"progress_{profile}.json"
            if path.exists():
                try:
                    self._profile_progress[profile] = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    # never silently overwrite a corrupt per-character file
                    backup_corrupt(path)
                    self._profile_progress[profile] = {}
            else:
                self._profile_progress[profile] = {}

    def _fresh_profile(self, profile: str) -> dict | None:
        """Current on-disk profile dict (FULL), or None when missing/unreadable.
        Writers that must never clobber data from partial memory use this as
        their base: the file is the truth, the memory cache is only a speed-up
        (a second instance / fresh Settings / a failed load must not overwrite
        a populated file with whatever happened to be cached)."""
        path = config_dir() / f"progress_{profile}.json"
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except (OSError, json.JSONDecodeError):
            pass
        return None

    def _profile_write(self, profile: str, key: str, value) -> None:
        """Disk-authoritative single-key profile write.

        The file is the truth, the memory cache is only a speed-up: reads the
        CURRENT progress_<profile>.json, applies exactly this one key, and
        writes back — a stale/partial in-memory copy must never drag the
        file's OTHER keys (orbs / hidden / waypoints / bests) back to an
        older state. Every profile setter funnels through here."""
        base = self._fresh_profile(profile)
        if base is None:
            base = dict(self._profile_progress.get(profile, {}))
        base[key] = value
        self._profile_progress[profile] = base
        self.save_profile_data(profile)

    def _profile_read(self, profile: str, key: str) -> list:
        """Current on-disk list for `key` (empty when missing/unreadable) —
        the base for a read-modify-write toggle, written back through
        _profile_write."""
        base = self._fresh_profile(profile)
        if base is None:
            base = self._profile_progress.get(profile, {})
        return list(base.get(key, []) or [])

    def save_profile_data(self, profile: str) -> None:
        try:
            path = config_dir() / f"progress_{profile}.json"
            raw = self._profile_progress.get(profile, {})
            if not raw:
                # A writer with NO in-memory reference (fresh/second instance
                # or a failed load) must never clobber a populated file with a
                # one-key/empty write: seed the base from the CURRENT file so
                # the write lands on top of the real data.
                fresh = self._fresh_profile(profile)
                if fresh:
                    raw = fresh
                    self._profile_progress[profile] = fresh
            # Clean, deduplicate and compact progress data
            data = {}
            for k, v in raw.items():
                if isinstance(v, list):
                    if v:
                        data[k] = sorted(set(v))
                elif isinstance(v, dict):
                    if v:
                        data[k] = v
                elif v:
                    data[k] = v
            atomic_write_json(path, data, compact_lists=True)
        except OSError:
            pass

    def save_profile_progress(self, profile: str, done_list: list[str]) -> None:
        # Disk-authoritative via _profile_write: the done_list may be fresh
        # while the cache is stale - writing onto the cached dict would drag
        # the OTHER keys (orbs / hidden) back to an older state.
        self._profile_write(profile, "poi_done", list(done_list))

    def get_poi_done_authoritative(self, profile: str | None = None) -> list[str]:
        """Disk-authoritative poi_done for the sync writers (orb/entity ticks).

        Re-reads progress_<profile>.json FRESH so a second instance or a
        fresh Settings object can never build a sync on stale/empty in-memory
        state: the file is the truth, the memory cache is only a speed-up.
        Refreshes the whole cached profile dict too, so a save right after
        keeps the other done-lists (orbs / hidden) intact. Falls back to the
        cache when the file is missing or unreadable.
        """
        if not profile:
            return self.poi_done
        path = config_dir() / f"progress_{profile}.json"
        try:
            if path.exists():
                disk = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(disk, dict):
                    self._profile_progress[profile] = disk
                    return disk.setdefault("poi_done", [])
        except (OSError, json.JSONDecodeError):
            pass
        self._ensure_profile_loaded(profile)
        return self._profile_progress[profile].setdefault("poi_done", [])

    def sync_done_list_safe(self, profile: str | None, done_list: list) -> bool:
        """False when the about-to-be-synced done-list is EMPTY while the
        on-disk profile file still holds done data - the partial-memory wipe
        signature (a second instance / fresh Settings that never loaded the
        profile must not wipe a populated file by syncing an empty base).
        True for missing/empty files and for any non-empty write. The >20%
        write-time guard in save_profile_data covers the partial-but-not-
        empty variants on top of this."""
        if not profile:
            return True
        path = config_dir() / f"progress_{profile}.json"
        try:
            if path.exists():
                disk = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(disk, dict) and disk.get("poi_done"):
                    return bool(done_list)
        except (OSError, json.JSONDecodeError):
            pass
        return True


    def save_dps_best(self, profile: str | None, dps_best: dict) -> None:
        if not profile:
            self.dps_best = dps_best
            self.save()
            return
        # Disk-authoritative single-key write (see _profile_write)
        self._profile_write(profile, "dps_best", dps_best)

    def get_dps_baseline(self, profile: str | None = None) -> dict:
        """The remembered training-dummy test for this profile ({} when none).

        Per profile because a baseline is a statement about THIS character's
        gear and rotation — a Warrior's reference is meaningless to a Mage.
        """
        if not profile:
            return self.dps_baseline
        self._ensure_profile_loaded(profile)
        prof = self._profile_progress[profile].get("dps_baseline")
        if prof is not None:
            return prof
        return self.dps_baseline

    def save_dps_baseline(self, profile: str | None, snapshot: dict) -> None:
        """Store the just-finished dummy test as the next one's reference."""
        if not profile:
            self.dps_baseline = snapshot
            self.save()
            return
        # Disk-authoritative single-key write (see _profile_write)
        self._profile_write(profile, "dps_baseline", snapshot)

    def get_dps_best_dummy(self, profile: str | None = None) -> dict:
        """The best dummy test this profile has ever run ({} when none).

        Separate from the last-test reference on purpose: those have opposite
        lifetimes. The reference is REPLACED by every run, because "did that
        change help" is a question about the run before this one; the best is
        only ever beaten, because "is this gear actually better" is a question
        about the whole history — and a bad run must never be able to lower it.
        """
        if not profile:
            return self.dps_best_dummy
        self._ensure_profile_loaded(profile)
        prof = self._profile_progress[profile].get("dps_best_dummy")
        if prof is not None:
            return prof
        return self.dps_best_dummy

    def save_dps_best_dummy(self, profile: str | None, snapshot: dict) -> None:
        """Record a new personal best for this profile (only ever a better run)."""
        if not profile:
            self.dps_best_dummy = snapshot
            self.save()
            return
        # Disk-authoritative single-key write (see _profile_write)
        self._profile_write(profile, "dps_best_dummy", snapshot)

    def get_speedrun_best(self, profile: str | None = None) -> dict:
        if not profile:
            return self.speedrun_best
        self._ensure_profile_loaded(profile)
        prof_best = self._profile_progress[profile].get("speedrun_best")
        if prof_best is not None:
            return prof_best
        return self.speedrun_best

    def save_speedrun_best(self, profile: str | None, speedrun_best: dict) -> None:
        # Sub-second times are dropped here, at the store, so no caller - the
        # overlay, a restore, a test double - can store one as a PB.
        speedrun_best = _clean_bests(speedrun_best)
        if not profile:
            self.speedrun_best = speedrun_best
            self.save()
            return
        # Disk-authoritative single-key write (see _profile_write)
        self._profile_write(profile, "speedrun_best", speedrun_best)

    def get_speedrun_boss_best(self, profile: str | None = None) -> dict:
        if not profile:
            return self.speedrun_boss_best
        self._ensure_profile_loaded(profile)
        prof_best = self._profile_progress[profile].get("speedrun_boss_best")
        if prof_best is not None:
            return prof_best
        return self.speedrun_boss_best

    def save_speedrun_boss_best(self, profile: str | None, boss_best: dict) -> None:
        # Sub-second times are dropped here, at the store, so no caller - the
        # overlay, a restore, a test double - can store one as a PB.
        boss_best = _clean_bests(boss_best)
        if not profile:
            self.speedrun_boss_best = boss_best
            self.save()
            return
        # Disk-authoritative single-key write (see _profile_write)
        self._profile_write(profile, "speedrun_boss_best", boss_best)

    def get_speedrun_history(self) -> list:
        """Shared cross-character kill log, newest last (capped on write).
        The kill log lives in moddata/speedrun_history.json — one store for
        every character. PBs remain per-character (get_speedrun_best).
        """
        return load_speedrun_history()

    def save_speedrun_history(self, runs: list) -> None:
        """Replace the shared kill log (see get_speedrun_history)."""
        write_speedrun_history(list(runs or []))


    def get_poi_done(self, profile: str | None = None) -> list[str]:
        if not profile:
            return self.poi_done
        self._ensure_profile_loaded(profile)
        return self._profile_progress[profile].setdefault("poi_done", [])

    def is_done(self, poi_id: str, profile: str | None = None) -> bool:
        """Return whether a profile contains a collected POI.

        Game/static IDs are normally cased consistently, but live memory and
        older profiles have used different casing.  Normalize both sides so a
        collected item cannot reappear merely because the renderer received a
        differently-cased ID.
        """
        needle = str(poi_id or "").casefold()
        return bool(needle) and any(
            str(done_id or "").casefold() == needle
            for done_id in self.get_poi_done(profile)
        )

    # --- dungeon orbs ---------------------------------------------------------
    # Dedicated per-character done-group for dungeon/rift secret orbs so they
    # never collide with overworld poi_done entries.
    def get_dungeon_orb_done(self, profile: str | None = None) -> list[str]:
        if profile:
            self._ensure_profile_loaded(profile)
            return self._profile_progress[profile].setdefault("dungeon_orb_done", [])
        return self.dungeon_orb_done


    def toggle_dungeon_orb_done(self, orb_id: str, profile: str | None = None) -> bool:
        if profile:
            # Disk-authoritative (see _profile_write/_profile_read)
            done_list = self._profile_read(profile, "dungeon_orb_done")
            if orb_id in done_list:
                done_list.remove(orb_id)
                done = False
            else:
                done_list.append(orb_id)
                done = True
            self._profile_write(profile, "dungeon_orb_done", done_list)
            return done
        done_list = self.dungeon_orb_done
        if orb_id in done_list:
            done_list.remove(orb_id)
            done = False
        else:
            done_list.append(orb_id)
            done = True
        self.save()
        return done

    def toggle_done(self, poi_id: str, profile: str | None = None) -> bool:
        if profile:
            # Disk-authoritative (see _profile_write/_profile_read)
            done_list = self._profile_read(profile, "poi_done")
            if poi_id in done_list:
                done_list.remove(poi_id)
                done = False
            else:
                done_list.append(poi_id)
                done = True
            self._profile_write(profile, "poi_done", done_list)
            return done
        done_list = self.poi_done
        if poi_id in done_list:
            done_list.remove(poi_id)
            done = False
        else:
            done_list.append(poi_id)
            done = True
        self.save()
        return done

    def get_entity_hidden_units(self, profile: str | None = None) -> list[str]:
        """Hidden mobs: per-character when a profile is attached, otherwise the
        global settings list (never a progress_default.json)."""
        if profile:
            self._ensure_profile_loaded(profile)
            return self._profile_progress[profile].setdefault("entity_hidden_units", [])
        return self.entity_hidden_units

    def toggle_unit_hidden(self, uid: str, hidden: bool, profile: str | None = None) -> None:
        if profile:
            # Disk-authoritative (see _profile_write/_profile_read)
            cur = set(self._profile_read(profile, "entity_hidden_units"))
            (cur.add if hidden else cur.discard)(uid)
            self._profile_write(profile, "entity_hidden_units", sorted(cur))
        else:
            cur = set(self.entity_hidden_units)
            (cur.add if hidden else cur.discard)(uid)
            self.entity_hidden_units = sorted(cur)
            self.save()

    def bulk_toggle_units_hidden(self, uids: list[str], hidden: bool, profile: str | None = None) -> None:
        if profile:
            # Disk-authoritative (see _profile_write/_profile_read)
            cur = set(self._profile_read(profile, "entity_hidden_units"))
            for uid in uids:
                (cur.add if hidden else cur.discard)(uid)
            self._profile_write(profile, "entity_hidden_units", sorted(cur))
        else:
            cur = set(self.entity_hidden_units)
            for uid in uids:
                (cur.add if hidden else cur.discard)(uid)
            self.entity_hidden_units = sorted(cur)
            self.save()

    def set_all_units_hidden(self, uids: list[str], hidden: bool, profile: str | None = None) -> None:
        if profile:
            # Disk-authoritative single-key write: the clear applies to what
            # is CURRENTLY on disk, not a stale copy (see _profile_write)
            self._profile_write(profile, "entity_hidden_units",
                                sorted(uids) if hidden else [])
        else:
            self.entity_hidden_units = sorted(uids) if hidden else []
            self.save()

    # --- companions ----------------------------------------------------------
    def get_companion_hidden_units(self, profile: str | None = None) -> list[str]:
        return self.companion_hidden_units

    def toggle_companion_hidden(self, uid: str, hidden: bool,
                                profile: str | None = None) -> None:
        """Disk-authoritative companion hide/show toggle.

        collection.json is the single source of truth and is never rewritten
        wholesale from memory (a stale in-memory copy is how the file got
        wiped before). This reads the CURRENT file, applies exactly this one
        unit to its category, and writes it back; the in-memory lists are
        synced to the written result so the UI stays consistent.
        """
        if not uid:
            return
        key = _categorize_companion(uid)
        cur = _read_collection_disk()
        lst = set(cur.get(key, []) or [])
        (lst.add if hidden else lst.discard)(uid)
        cur[key] = sorted(lst)
        try:
            atomic_write_json(_collection_path(), cur, compact_lists=True)
        except OSError:
            pass
        self.pet_hidden_units = sorted(set(cur.get("pets", []) or []))
        self.mount_hidden_units = sorted(set(cur.get("mounts", []) or []))
        self.glider_hidden_units = sorted(set(cur.get("gliders", []) or []))


