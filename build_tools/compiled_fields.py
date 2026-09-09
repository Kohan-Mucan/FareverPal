"""Do the compiled shims still carry every field the data layer reads?

The compiler cherry-picks fields per sheet (`compiler.py`'s `sheets` table):
`row = {f: line[f] for f in fields if line.get(f) is not None}`. Anything not
listed is dropped SILENTLY, and `cdb.sheet()` then falls back to the loose
JSON — so in a dev checkout a trimmed field can keep working while the frozen
build (which bundles only the shims) loses it. That is how `props` went
missing on `raw_items` and the Food/Elixir durations had to be re-read from the
source sheet.

This module does not trust a hand-written list of dependencies. It MEASURES
them:

  1. snapshot every compiled payload's field set (what survived),
  2. swap each shim's `DATA` for wrappers that record every field the data
     layer ASKS for on a row (present or not),
  3. exercise the data layer's public API over every id,
  4. report each field requested where NO row of that payload carries it while
     the compile INPUT sheet does — i.e. a dependency the compiler trims.

Per-row absence (an optional field, like `level` on a third of the drops rows)
is normal and not reported; only payload-wide absence counts, because that is
what a whitelist can cause and what a reader can never recover from.

Run it directly (`python build_tools/check_compiled_fields.py`) or import
`violations()`; `build_tools/verify_assets.py` runs it as part of the build.
"""
from __future__ import annotations

import importlib
import json
import sys
import traceback
from collections import defaultdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

SHIMS = ("raw_data", "raw_units", "raw_items", "raw_skills", "raw_craft",
         "raw_locs", "raw_codex", "raw_item_drops")

# The compile INPUTS behind each shim: where the field came from before the
# compiler decided whether to keep it. Game sheets first, then the scan-made
# JSONs the compiler merges.
SOURCE_FILES = {
    "raw_items": ["item.json"],
    "raw_units": ["unit.json", "lootTable.json"],
    "raw_skills": ["skill.json"],
    "raw_data": ["rarity.json", "zone.json", "unitType.json", "constant.json",
                 "counter.json", "dungeons.json", "chest_locs.json",
                 "gatherable_locs.json", "critter_locs.json", "mob_locs.json",
                 "orb_positions.json", "poi_locs.json"],
    "raw_item_drops": ["item_drops.json"],
    "raw_craft": ["recipe.json", "job.json"],
    "raw_codex": ["codex.json"],
    "raw_locs": ["chest_locs.json", "gatherable_locs.json", "mob_locs.json",
                 "poi_locs.json", "orb_positions.json", "critter_locs.json"],
}

# Reads that are deliberately tolerated, keyed (shim, payload, field) with the
# reason they are safe. Keep this SHORT and each reason specific — an entry
# here means "a payload-wide absence in this one field is expected".
EXCEPTIONS: dict[tuple[str, str, str], str] = {
    # KNOWN DEBT, not a harmless one: the read is source-sheet-first on
    # purpose, but FareverPal.spec bundles assets/data/*.json ONLY in
    # raw_data-less fallback mode — so in the normal frozen build
    # _item_effect_durations has no source sheet AND the compiled rows have no
    # `props`, i.e. consumable status durations (the Feast's 1H vs the 15M
    # norm) are lost there. Clearing it means adding `props` (or just
    # props.effects) to compiler.py's `items` field list and recompiling.
    ("raw_items", "items", "props"):
        "consumable durations are read source-first (catalog.py::"
        "_item_effect_durations); the loose item.json is a compile input the "
        "normal build does not bundle, so the compiled fallback is empty in "
        "the frozen build — known debt, fix by keeping props in the compiler's "
        "items field list",
    ("raw_data", "zone", "texts"):
        "dual-shape fallback: the compiler bakes `name`, and data/names.py "
        "reads texts.name only when a row has no name at all (the loose "
        "source sheet's shape) — dropping `texts` loses nothing the baked "
        "name does not already cover",
}


class TrackedRow(dict):
    """A compiled row that records the fields asked of it."""

    __slots__ = ("_shim", "_key", "_sink", "_callers")

    def __init__(self, rows, shim: str, key: str, sink: dict, callers: dict):
        super().__init__(rows)
        self._shim, self._key, self._sink = shim, key, sink
        self._callers = callers

    def _note(self, field):
        miss = (self._shim, self._key, field)
        self._sink[miss] += 1
        if miss not in self._callers:
            frames = [f for f in traceback.extract_stack()[:-1]
                      if "/farever_companion/" in f.filename.replace("\\", "/")
                      and "/data/raw_" not in f.filename.replace("\\", "/")]
            self._callers[miss] = " <- ".join(
                f"{f.filename.replace(chr(92), '/').split('farever_companion/')[-1]}:{f.lineno}"
                for f in frames[-3:]) or "?"

    def __getitem__(self, k):
        if k not in self:
            self._note(k)
        return super().__getitem__(k)

    def get(self, k, default=None):
        if k not in self:
            self._note(k)
        return super().get(k, default)


def classify(obj) -> str:
    """'map' = {id: row}, 'wrap' = {label: rows}, 'list' = [row], 'row',
    'scalar' = anything that is not a row container (a stamp, a number)."""
    if isinstance(obj, dict):
        vals = list(obj.values())
        if vals and all(isinstance(v, dict) for v in vals):
            return "map"
        if vals and all(isinstance(v, list) for v in vals):
            return "wrap"
        return "row"
    if isinstance(obj, list):
        return "list"
    return "scalar"


def wrap(obj, shim, key, sink, callers):
    """Same shape, every row replaced by a TrackedRow."""
    kind = classify(obj)
    if kind == "map":
        return {k: TrackedRow(v, shim, key, sink, callers) for k, v in obj.items()}
    if kind == "wrap":
        return {k: wrap(v, shim, key, sink, callers) for k, v in obj.items()}
    if kind == "list":
        return [wrap(r, shim, key, sink, callers) for r in obj]
    if kind == "scalar":
        return obj
    return TrackedRow(obj, shim, key, sink, callers)


def fields_of(obj) -> set[str]:
    """Every field name any row in a payload carries (mirrors `classify`)."""
    kind = classify(obj)
    out: set[str] = set()
    if kind == "map":
        for v in obj.values():
            out |= set(v.keys())
    elif kind == "wrap":
        for v in obj.values():
            out |= fields_of(v)
    elif kind == "list":
        for r in obj:
            out |= fields_of(r)
    elif kind == "row":
        out = set(obj.keys())
    return out


def _shim_modules() -> dict:
    mods = {}
    for name in SHIMS:
        try:
            mods[name] = importlib.import_module(f"farever_companion.data.{name}")
        except ImportError:
            pass
    return mods


def _clear_caches(*modules) -> None:
    seen = set()
    for mod in modules:
        for obj in list(vars(mod).values()):
            fn = getattr(obj, "cache_clear", None)
            if fn is not None and id(obj) not in seen:
                seen.add(id(obj))
                try:
                    fn()
                except Exception:
                    pass


def _source_fields(shim: str) -> set[str]:
    """Fields named by a shim's compile inputs (loose sheets on disk)."""
    try:
        from farever_companion import paths
        dirs = [paths.sheets_dir(), paths.sheets_dir().parent]
    except Exception:
        return set()
    out: set[str] = set()
    for fname in SOURCE_FILES.get(shim, []):
        for d in dirs:
            p = d / fname
            if not p.exists():
                continue
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows = data.get("lines") if isinstance(data, dict) else None
            if rows is None and isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        rows = v
                        break
                    if isinstance(v, dict) and v:
                        rows = list(v.values())
                        break
            for r in rows or []:
                if isinstance(r, dict):
                    out |= set(r.keys())
    return out


def _exercise() -> None:
    """Call the data layer's public API over every id, so its field reads
    happen with tracking installed. Qt-free on purpose: this runs in the build
    before PyInstaller, and `core/`+`data/` stay importable without PySide6."""
    from farever_companion.data import (cdb, classes, codex, dungeons,
                                        encounters, names, skills as S,
                                        soulwell, units)
    from farever_companion.data.items import catalog, craft, labels, sources, stats

    _clear_caches(cdb, classes, codex, dungeons, names, units, S, soulwell,
                  catalog, craft, labels, sources, stats)

    for sheet in ("item", "unit", "skill", "rarity", "zone", "unitType",
                  "constant", "counter", "lootTable", "items", "units",
                  "skills", "atlas"):
        for fn in (cdb.lines, cdb.by_id):
            try:
                fn(sheet)
            except Exception:
                pass
    for name in ("items", "enemies", "skills"):
        try:
            cdb.display_data(name)
        except Exception:
            pass

    # Soulwell tunables (the constant + counter sheets): every reader
    # the buff-timer feature ships, so a trimmed `v` on the constant
    # rows or a dropped `luckParams` on the counter rows fails the
    # build like any other trim.
    for fn in (soulwell.soulwell_tunables, soulwell.soulwell_buff_ids,
               soulwell.luck_counters, soulwell.reputation_duration_bonus):
        try:
            fn()
        except Exception:
            pass
    for rarity in ("rare", "epic"):
        try:
            soulwell.buff_duration_minutes(rarity)
            soulwell.reputation_gain(rarity)
        except Exception:
            pass
    for status in (soulwell.soulwell_buff_ids() or []):
        try:
            counter = soulwell.luck_counter_for_status(status) or ""
            soulwell.luck_chance(counter, 3)
            soulwell.luck_scope(counter)
        except Exception:
            pass

    for iid in [r.get("id") for r in (catalog.items() or []) if r.get("id")]:
        for fn in (catalog.item, catalog.weapon_upgrade_trait,
                   catalog.item_effect_duration, craft.recipe, craft.is_craftable,
                   classes.item_aptitudes, stats.gear_ratings, stats._stat_labels,
                   sources.item_source_max_level, sources.item_scale_max_level,
                   sources.item_vendor_levels, sources.acquisition_note,
                   sources.no_source_reason):
            try:
                fn(iid)
            except Exception:
                pass
        # the row-taking stat math, called the way the item page calls it
        # (from `catalog.item`, i.e. with the id the page already holds)
        row = catalog.item(iid) or {}
        for fn in (stats.item_fixed_level, stats.gear_stats, stats.upgrade_ladder,
                   stats._atb_budget, stats._is_authored_affixes,
                   labels.is_gear, labels.own_stats, labels.weapon_attack):
            try:
                fn(row)
            except Exception:
                pass
    for fn in (catalog.search, catalog.types, catalog.rarities,
               catalog.resolve_food_info):
        try:
            fn("")
        except Exception:
            pass

    for uid in [r.get("id") for r in (cdb.lines("unit") or []) if r.get("id")]:
        for fn in (units.is_boss, units.is_elite, units.drops_spark, units.is_unique,
                   units.boss_loot_table, codex.find_unit_region,
                   dungeons.get_dungeon_info, encounters.resolve):
            try:
                fn(uid)
            except Exception:
                pass

    for sid in [r.get("id") for r in (cdb.lines("skill") or []) if r.get("id")]:
        for fn in (S.skill_row, S.skill_meta, S.skill_moves, S.skill_type,
                   S.skill_rank_descriptions):
            try:
                fn(sid)
            except Exception:
                pass
        for rank in range(0, 6):
            try:
                S.skill_description(sid, rank=rank)
            except Exception:
                pass

    for fn in (units.spark_unit_ids, units.resolve_hero_class, codex.unit_regions,
               codex.enemies_data, codex.region_names, codex.codex_order,
               names._status_to_item, names._items_by_status, craft.jobs,
               craft.craft_levels, craft.craft_jobs, dungeons.load_dungeons,
               dungeons.unit_to_dungeon_map, stats.gear_scaling,
               stats._rarity_upgrade_caps, stats._gear_upgrades,
               labels.categories, labels.gear_classes, labels.gear_slots):
        try:
            fn()
        except Exception:
            pass


def triage(misses: dict, before: dict, callers: dict,
           source_fields, exceptions: dict | None = None) -> tuple[list, list, list]:
    """Classify recorded reads -> (violations, excused, notes).

    A read only counts when NO row of the payload carries the field (a
    payload-wide absence; optional per-row fields like `level` on the drops
    rows are normal). It is then one of:

    * a TRIMMED dependency (`violations`) when the compile input names the
      field — the compiler had it and its field list dropped it,
    * `excused` when the key is in EXCEPTIONS (documented, deliberate),
    * a `note` when no compile input names it either — the reader is expected
      to fall back by design (codex's optional coords/icon) and failing the
      build on it would be a false alarm.
    """
    exceptions = EXCEPTIONS if exceptions is None else exceptions
    get_source = source_fields if callable(source_fields) else (lambda shim: source_fields.get(shim, set()))
    excused, violations, notes = [], [], []
    for miss in sorted(misses, key=lambda m: (-misses[m], m)):
        shim, key, field = miss
        survived = before.get(shim, {}).get(key)
        if survived is None or field in survived:
            continue                      # per-row absence: not a trim
        in_source = field in get_source(shim)
        row = (shim, key, field, misses[miss], callers.get(miss, "?"), in_source)
        if miss in exceptions:
            excused.append(row)
        elif in_source:
            violations.append(row)
        else:
            notes.append(row)
    return violations, excused, notes


def sweep() -> dict:
    """Run the whole measurement. Returns:
        {misses, before, callers, excused, violations}
    `violations` = [(shim, payload, field, reads, caller, in_source)] for every
    payload-wide absence that is not in EXCEPTIONS.
    """
    shims = _shim_modules()
    misses: dict[tuple[str, str, str], int] = defaultdict(int)
    callers: dict[tuple[str, str, str], str] = {}

    before: dict[str, dict[str, set]] = {}
    for name, mod in shims.items():
        data = getattr(mod, "DATA", None)
        if isinstance(data, dict):
            before[name] = {k: fields_of(v) for k, v in data.items()}

    saved = {}
    try:
        for name, mod in shims.items():
            data = getattr(mod, "DATA", None)
            if not isinstance(data, dict):
                continue
            saved[name] = data
            mod.DATA = {k: wrap(v, name, k, misses, callers) for k, v in data.items()}
        _exercise()
    finally:
        for name, data in saved.items():
            shims[name].DATA = data
        _clear_caches(*shims.values())

    violations, excused, notes = triage(misses, before, callers, _source_fields)
    return {"misses": misses, "before": before, "callers": callers,
            "excused": excused, "violations": violations, "notes": notes}


def violations() -> list:
    """Just the trims (what the build must fail on)."""
    return sweep()["violations"]
