import base64
import json
import re
import zlib
from pathlib import Path

# The compiler is STANDALONE: it has to run with the app package unimportable,
# because it is the thing that GENERATES part of that package. So the shared
# game rules (unit-flag bits, the premium-id / guild-merchant predicates, the
# codex kind sets, the rating labels, the payload-pure item derivations) live in
# `game_rules.py` beside this file, and the app reads a GENERATED copy of it:
# `_bundle_shared_rules` writes that copy into the package on every build. The
# app's own modules keep importing the rules exactly as before (their current
# homes re-export them), so no call site moved.
#
# The only app-side knowledge left in this file is PACKAGE_DIRNAME below — the
# one place the package's directory name is written down.
from game_rules import (
    RATING_ATTR_LABELS, SPARK_PROXY_FLAG_BIT, UNIQUE_FLAG_BIT, crafted_item_ids,
    heroic_boss_items, is_guild_merchant_source, is_no_codex, is_premium_shop_id,
    is_special_flag, is_unreleased_kind, recipe_output_ids_from_payload,
    shop_item_ids_from_payload)

#: The app package's directory name — written down ONCE, here, because the
#: compiler may not import the package to ask. `tests/test_compiler.py` pins it
#: against the real directory and against the runtime's `paths.data_root()`, so
#: a rename has to move all three or fail loudly.
PACKAGE_DIRNAME = "farever_companion"

#: The canonical shared-rules leaf, bundled into the package on every build.
RULES_SOURCE = Path(__file__).resolve().parent / "game_rules.py"


def _embed_shim(stem: str, payload: dict) -> str:
    """Render a self-contained loader shim for `stem`.

    The payload is embedded in the module as zlib+base85-compressed JSON, so
    each shim stays a single self-contained file that loads in one
    decompress + json.load at import time.
    """
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    comp = zlib.compress(raw, 9)
    b85 = base64.b85encode(comp).decode("ascii")
    lines = "\n    ".join(f'b"{b85[i:i + 76]}"' for i in range(0, len(b85), 76))
    return f'''# -*- coding: utf-8 -*-
"""Compiled game data for {stem}, embedded in this module.

The payload is zlib+base85-compressed JSON embedded as a literal.
Decompressed once at import time — `from . import raw_X; raw_X.DATA` keeps
working everywhere (dev + frozen).
"""
import base64
import json
import zlib

_PAYLOAD = (
    {lines}
)


def _payload_size_kb() -> float:
    """Size of the embedded compressed payload (KB)."""
    return len(_PAYLOAD) / 1024.0


try:
    DATA = json.loads(zlib.decompress(base64.b85decode(_PAYLOAD)))
except (ValueError, zlib.error) as _e:
    raise ImportError("{stem}: corrupted embedded payload (re-run compiler.py)") from _e
'''


def _payload_meta() -> dict:
    """Provenance stamp embedded in every compiled shim: which game-data
    version this payload was built from. `data_version` mirrors
    assets/data/_version.json's extraction timestamp (falling back to
    codex.json's), so a stale raw_*.py is easy to spot by comparing it against
    the current assets/data/_version.json before rebuilding.

    Deliberately contains NO wall-clock timestamp: the payload must be a pure
    function of the game data so rebuilds of unchanged data are byte-identical
    (and `git status` stays clean after every build-mod). Git history is the
    record of *when* a shim was generated."""
    data_version = "unknown"
    root = Path(__file__).parent
    try:
        vraw = json.loads((root / "assets" / "data" / "_version.json").read_text(encoding="utf-8"))
        if isinstance(vraw, dict) and vraw.get("generated_at"):
            data_version = str(vraw["generated_at"])
    except Exception:
        pass
    if data_version == "unknown":
        try:
            craw = json.loads((root / "assets" / "data" / "codex.json").read_text(encoding="utf-8"))
            if isinstance(craw, dict) and craw.get("generated_at"):
                data_version = str(craw["generated_at"])
        except Exception:
            pass
    return {
        "compiler": "compiler.py",
        "data_version": data_version,
    }


def _write_embedded_data(output_dir: Path, stem: str, payload: dict,
                         dev_dir: Path | None = None) -> None:
    """Write {stem}.py (embedded payload), plus {stem}.json as a dev-only
    copy when `dev_dir` is given (opt-in via compiler --dev-copy). Without
    it only the shim is written — no tmp_preview/ output, keeping the
    shipped module dir free of loose plain-text data either way."""
    payload = dict(payload)
    payload["__meta__"] = _payload_meta()
    p = output_dir / f"{stem}.py"
    p.write_text(_embed_shim(stem, payload), encoding="utf-8")
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if dev_dir is None:
        print(f"Written: {p.name} ({len(raw) / 1024:.0f} KB)")
        return
    dev_dir.mkdir(parents=True, exist_ok=True)
    json_p = dev_dir / f"{stem}.json"
    json_p.write_bytes(raw)
    print(f"Written: {p.name} (+{json_p.name} dev copy in {dev_dir.name}/, {len(raw) / 1024:.0f} KB)")


# --- the shared-rules bundle ------------------------------------------------#
# `game_rules.py` (beside this file) is the canonical home of the rules the app
# and the build both spell. The app cannot import a root-level module, so the
# build bundles it into the package as `rules.py`. Two properties matter:
#
#   * the banner is COMMENTS, not a docstring — `from __future__ import
#     annotations` stays the first statement after the copy's own module
#     docstring, so the bundled file still parses;
#   * no timestamp, for the same reason `_payload_meta` has none: the copy is a
#     pure function of `game_rules.py`, so rebuilding unchanged rules is
#     byte-identical and the test below can pin the two together.
_RULES_BANNER = '''\
# -*- coding: utf-8 -*-
# GENERATED FILE - DO NOT EDIT.
#
# The app's copy of the shared game rules, bundled from `game_rules.py` at the
# repo root by `compiler.py` on every build. The rules live OUTSIDE the app
# package so the build can load them without that package being importable (the
# build is what generates it), and the app keeps importing them here.
#
# Edit `game_rules.py` and rebuild: an edit to THIS file is reverted by the next
# `python compiler.py`, and `tests/test_compiler.py` fails while the two differ.

'''


def bundled_rules_source() -> str:
    """The exact text of the app's `rules.py`: the banner plus `game_rules.py`."""
    return _RULES_BANNER + RULES_SOURCE.read_text(encoding="utf-8")


def _bundle_shared_rules(package_dir: Path) -> Path:
    """Write the shared-rules copy into the app package; returns the path.

    Called from `__main__` (the entry point `build-mod.bat` and
    `Update_Raw_Data.bat` use) and deliberately NOT from `compile_to_py`, whose
    contract is that it writes only under the `output_file` it is handed — a
    test asserts that, and a caller compiling into a tmp dir must not be able to
    touch the checkout.
    """
    p = package_dir / "rules.py"
    p.write_text(bundled_rules_source(), encoding="utf-8")
    return p

def _prune_entries(rows, using_atlas: bool) -> list:
    """Strip the per-row gfx/icon fallbacks once the atlas carries them."""
    if not using_atlas:
        return rows
    for row in rows:
        row.pop("gfx", None)
        row.pop("icon", None)
    return rows


def _write_data_file(output_dir: Path, filename: str, data_dict: dict,
                     dev_dir: Path | None, using_atlas: bool) -> None:
    """Write one shim.

    The payload is embedded inside the shim module itself as zlib+base85
    compressed JSON: that keeps each raw_X.py a single self-contained file and
    import fast (one zlib.decompress + json.load, ~5x faster than the old
    Python literal), and `from . import raw_X; raw_X.DATA` keeps working
    everywhere (dev + frozen) without touching any consumer.
    """
    stem = Path(filename).stem
    payload = {}
    for key, val in data_dict.items():
        if key in ["units", "items", "skills", "lootTable"]:
            val = _prune_entries(val, using_atlas)
        payload[key] = val
    _write_embedded_data(output_dir, stem, payload, dev_dir)


def _load_atlas_map(atlas_dir: Path, category: str) -> dict:
    """atlas_<category>*.json, merged.

    Merge ALL matching files (multi-sheet packs: atlas_items_01.json,
    atlas_items_02.json, ...) instead of returning the first one found.
    """
    merged = {}
    for ap in sorted(atlas_dir.glob(f"atlas_{category}*.json")):
        if not ap.exists():
            continue
        try:
            content = json.loads(ap.read_text(encoding="utf-8"))
            if isinstance(content, dict):
                merged.update(content)
        except Exception:
            continue
    return merged


def _collection_source(entry: dict) -> str:
    """Human-readable acquisition source for a derived collection row,
    mirroring the codex resolver's badge titles (Vendor: / Chest: / Mob
    Drop: / Dungeon:)."""
    v = entry.get("vendor_npcs") or entry.get("vendor_npc")
    if isinstance(v, dict):
        v = [v]
    if isinstance(v, list) and v:
        first = v[0] if isinstance(v[0], dict) else {}
        return f"Vendor: {first.get('name') or 'Shop NPC'}"
    c = entry.get("chest_locs") or entry.get("chest_loc")
    if isinstance(c, dict):
        c = [c]
    if isinstance(c, list) and c:
        first = c[0] if isinstance(c[0], dict) else {}
        cid = first.get("id") or first.get("chest_id") or "World Chest"
        return f"Chest: {cid}"
    df = entry.get("drops_from")
    if isinstance(df, list) and df:
        names = []
        for d in df:
            if isinstance(d, dict) and d.get("name") and d["name"] not in names:
                names.append(d["name"])
        if names:
            return "Drops from " + ", ".join(names)
    if entry.get("dungeon_name"):
        return f"Dungeon: {entry['dungeon_name']}"
    ach = entry.get("achievement")
    if isinstance(ach, dict) and ach.get("name"):
        return f"Achievement · {ach['name']}"
    return "Wild spawn" if entry.get("region") == "Pets" else "World drop"


# Cash-shop / early-access premium id pattern — mirrors the runtime authority
# (farever_companion/data/codex.py's _SHOP_ID_RE / is_shop_item). The runtime's
# canonical shop set is derived from the item drops index's vendor rows
# (sources.shop_item_ids()), and `_compiled_shop_ids` below derives the SAME set
# from the same JSON; this pattern only backstops ids the drops index never
# records as a shop offer. (The dead shop.json sheet was retired 2026-10-05:
# the dump never shipped it, so raw_shop was a placeholder.) The pattern
# itself is `rules.PREMIUM_SHOP_ID_RE`, applied through
# `rules.is_premium_shop_id` below rather than restated here.

def _compiled_shop_ids(data_new: Path, data_clean: Path) -> frozenset[str]:
    """The drops index's shop-ONLY id set, derived exactly as the runtime does.

    `rules.shop_item_ids_from_payload` is the single implementation of the
    predicate (the runtime's `shop_item_ids()` calls that same function over the
    live payload), and the compiler imports it from the stdlib-only leaf rather
    than from `data.items.sources` — the data layer must not be importable at
    build time. The index is read here because the collection catalog is built
    ~480 lines before the drops shim is written, and the crafted-id set comes
    from the same payload plus the craft.json recipe outputs. Missing index ->
    empty set, matching the runtime's own `frozenset()` fallback.
    """
    drops_p = data_new / "item_drops.json"
    if not drops_p.exists():
        drops_p = data_clean / "item_drops.json"
    if not drops_p.exists():
        return frozenset()
    try:
        drops = json.loads(drops_p.read_text(encoding="utf-8"))
    except Exception:
        return frozenset()
    return shop_item_ids_from_payload(
        drops, crafted_item_ids(drops, _recipe_output_ids(data_new, data_clean)))


def _recipe_output_ids(data_new: Path, data_clean: Path) -> frozenset[str]:
    """Ids any recipe produces, from craft.json — the payload-pure derivation
    in `rules` (the runtime reaches the same function through `craft`'s rows),
    so a change cannot drift. Missing sheet -> empty set, matching the
    runtime's lazy fallback."""
    craft_p = data_new / "craft.json"
    if not craft_p.exists():
        craft_p = data_clean / "craft.json"
    if not craft_p.exists():
        return frozenset()
    try:
        lines = json.loads(craft_p.read_text(encoding="utf-8")).get("lines", [])
    except Exception:
        return frozenset()
    return recipe_output_ids_from_payload(lines)


def _stamp_heroic_boss_edges(drops_data: dict, loot_rows) -> None:
    """Add the heroic boss edge to items the live scan recorded NO rows for.

    The scan records drop ROLLS; it never saw the boss -> `<boss>_HM` edge, so
    an Epic `_E<Faction>` set piece a heroic boss guarantees had no `drops`
    row and its card read "No drop sources found." The lootTable sheet is the
    ground truth (game_rules.heroic_boss_items) and the compiler already holds
    it, so stamp the edge here and every consumer of the compiled index sees
    it — not just the one card that used to read the sheet live.

    Only items with NO recorded rows are touched: whatever the scan captured
    stays exactly as it is, so this can neither drop nor rewrite a real roll.
    The boss source / `_HM` table are appended to the index only if missing,
    so existing indices are untouched.
    """
    items = drops_data.get("items") or {}
    tables_by_id = heroic_boss_items(loot_rows)
    if not items or not tables_by_id:
        return
    sources = drops_data.setdefault("_sources", [])
    tables = drops_data.setdefault("_tables", [])
    sidx = {s.get("id"): i for i, s in enumerate(sources)}
    tidx = {t: i for i, t in enumerate(tables)}
    for tid, rolled in tables_by_id.items():
        boss = tid[:-3]
        si = sidx.get(boss)
        if si is None:
            si = len(sources)
            sources.append({"id": boss, "kind": "unit", "name": boss})
            sidx[boss] = si
        ti = tidx.get(tid)
        if ti is None:
            ti = len(tables)
            tables.append(tid)
            tidx[tid] = ti
        for iid in rolled:
            it = items.get(iid)
            if not isinstance(it, dict) or it.get("drops"):
                continue          # no row of its own -> stamp; else leave alone
            it["drops"] = [{"l": -1, "p": 0.01, "s": si, "t": ti}]


def _collection_catalog_from_codex(data_clean: Path, data_new: Path,
                                   item_rows: list,
                                   ach_rewards: dict | None = None,
                                   shop_ids: frozenset[str] = frozenset()
                                   ) -> dict | None:
    """Derive the collection catalog (mounts / gliders / wild companions) from
    codex.json. Row ids stay the canonical game ids, so account sync and the
    website keep matching. Returns None when the codex is unavailable, letting
    the caller fall back to {}.

    `shop_ids` is the drops index's shop-only set (`_compiled_shop_ids`); a row
    in it counts as obtainable even though codex.json flags it 'unreleased'.
    """
    codex_p = data_clean / "codex.json"
    if not codex_p.exists():
        codex_p = data_new / "codex.json"
    try:
        codex = json.loads(codex_p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(codex, dict):
        return None
    codex = codex.get("codex") or codex
    if not isinstance(codex, dict):
        return None

    item_rarity = {r["id"]: r.get("rarity") for r in item_rows if r.get("id")}
    rows = []
    for eid, entry in codex.items():
        if not isinstance(entry, dict):
            continue
        etype = entry.get("type")
        if etype == "Mount":
            cat = "mounts"
        elif etype == "Glider":
            cat = "gliders"
        elif etype == "Critter" and entry.get("is_critter"):
            cat = "companions"
        else:
            continue
        eid = str(entry.get("id") or eid)
        # TODO placeholders and the YellowRabbits spawner artifact are not real
        # collectibles (the CDB companion set excludes the same row).
        if eid.upper().startswith("TODO") or eid == "YellowRabbits":
            continue
        subtype = entry.get("group") or ""
        if not subtype and "_" in eid:
            subtype = eid.split("_")[1] if len(eid.split("_")) > 1 else eid
        coords = entry.get("locations") or entry.get("coords") or []
        # codex.json flags achievement-only rewards and cash-shop items as
        # 'unreleased' (its scanner doesn't know about either), but both ARE
        # obtainable in-game — mirror the codex payload's reflag (kind ->
        # 'achievement' for coords-less rewards) and the runtime's
        # is_shop_item (the drops index's shop-only set OR the premium id
        # pattern) so the catalog agrees with the Codex Collection views
        # instead of listing them [unreleased].
        is_ach_reward = bool(ach_rewards and eid in ach_rewards)
        # The same union the runtime's codex.is_shop_item applies: the drops
        # index's shop-only set OR the premium id pattern. Checking only the
        # pattern (before this) dropped every drops-index shop row whose id
        # carries no premium token.
        is_shop = eid in shop_ids or is_premium_shop_id(eid)
        row = {
            "id": eid,
            "name": entry.get("name") or eid,
            "category": cat,
            "subtype": subtype or cat[:-1].capitalize(),
            "obtainable": not is_unreleased_kind(entry.get("kind"))
                          or (is_ach_reward and not coords)
                          or is_shop,
            "source": _collection_source(entry),
        }
        rarity = item_rarity.get(eid)
        if rarity:
            row["rarity"] = rarity
        if entry.get("icon"):
            row["icon"] = entry["icon"]
        if coords:
            row["coords"] = coords
        rows.append(row)
    return {"version": "codex", "items": rows}


# --- per-sheet compilation --------------------------------------------------#
# One builder per sheet family. These used to be inline in compile_to_py, which
# grew to ~1150 lines doing every sheet by hand; each builder takes its inputs
# and returns the rows its sheets contribute, so the pipeline below reads as
# the list of things it compiles.

# The fields each standard sheet keeps (names resolve into the rows; texts and
# gfx are handled per sheet name in _compile_standard_sheets).
SHEET_FIELDS = {
    "units": ["id", "type", "lvl", "maxLvl", "faction", "flags"],
    "unitType": ["id", "name", "lootTable"],
    # props: only gearUpgrades is read (data/items/stats.py). The rest of the
    # subtree (generationChance/iLevelBonus/sellPriceFactor) ships unread -
    # ratcheted into the hygiene suite's baked-key census; an older comment
    # here claimed it "feeds the loot predictor", but no loot predictor
    # exists in the tree.
    "rarity": ["id", "color", "props"],
    # parent/type ride along: geo/zones.py resolves each
    # SubLocation (type 2) to its parent Location via
    # zone_parent_map(), and that read has no loose-sheet
    # fallback in the frozen build.
    "zone": ["id", "name", "parent", "type"],
    "items": ["id", "rarity", "type"],
    "skills": ["id", "type", "nature"],
    # Tuning constants (GearUpgrades material costs, WorldLootLevel, ...)
    "constant": ["id", "v"],
    # Luck counters (the Soulwell buffs' per-stack chance math): only
    # the `luckParams` block is a tunable, and only the five Luck_*
    # rows carry one, so most rows bake as bare ids. data/soulwell.py
    # is the reader.
    "counter": ["id", "luckParams"],
}

# Hardcoded fallback region names
KNOWN_REGION_NAMES = {
    "Z1_Region": "Skover Island",
    "Z2_Region": "Valley of Eternal Autumn",
    "Z3_Region": "Crimson Island",
    "CrimsonIsland_Region": "Crimson Island",
}


def _unit_type_names(data_new: Path) -> tuple[dict, dict]:
    """`(names, gfx)` from unitType.json — what a unit row falls back to naming
    itself by when its own sheet carries no name."""
    ut_p = data_new / "unitType.json"
    ut_names = {}
    ut_gfx = {}
    if ut_p.exists():
        ut_raw = json.loads(ut_p.read_text(encoding="utf-8"))
        for r in ut_raw.get("lines", []):
            if r.get("name"): ut_names[r["id"]] = r["name"]
            if r.get("gfx"): ut_gfx[r["id"]] = r["gfx"]
    return ut_names, ut_gfx


def _named_boss_ids(data_new: Path) -> set:
    """Units whose id matches a loot-table id — the only "this is a boss"
    signal the unit sheet has, and the row pruning below must keep them."""
    named_boss_ids = set()
    lt_p = data_new / "lootTable.json"
    if lt_p.exists():
        try:
            lt_raw = json.loads(lt_p.read_text(encoding="utf-8"))
            named_boss_ids = {r["id"] for r in lt_raw.get("lines", [])}
        except: pass
    return named_boss_ids


def _compile_standard_sheets(data_new: Path, data_clean: Path,
                             unit_region_map: dict) -> dict:
    """Compile the standard sheets, narrowed to the fields the app reads.

    The per-name handling is where each sheet's shape is decided: unit rows get
    a resolved name and region, item rows get their buff-status refs, and skill
    rows carry everything the description resolver touches (the loose
    skill.json is a compile input only and is not bundled — see
    FareverPal.spec). Returns {sheet name: rows}.
    """
    ut_names, ut_gfx = _unit_type_names(data_new)
    named_boss_ids = _named_boss_ids(data_new)
    out: dict[str, list] = {}
    for name, fields in SHEET_FIELDS.items():
        # Mapping plural internal names to singular CDB filenames
        json_name = "unit" if name == "units" else name.rstrip('s') if name in ["items", "skills"] else name
        p = data_new / f"{json_name}.json"
        if not p.exists():
            p = data_clean / f"{json_name}.json"

        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            rows = []
            for line in raw.get("lines", []):
                uid = line.get("id")
                fl = line.get("flags", 0)
                # Skip compiling units the sheet marks as not-for-codex
                # (Dummy, the internal `_Z*D_` variants, the portal)
                if name == "units" and is_no_codex(fl):
                    continue
                
                # Aggressive Pruning: Skip non-boss/non-unique internal unit types
                if name == "units":
                    utype = line.get("type")
                    is_special = is_special_flag(fl) or (uid in named_boss_ids)
                    if utype in ("Totem", "Environment", "Trigger", "Marker") and not is_special:
                        continue

                # Only include fields that have a value
                row = {f: line[f] for f in fields if line.get(f) is not None}
                
                # Bake the name directly into the row to save runtime logic
                if name in ["units", "items", "skills"]:
                    row["name"] = (line.get("texts") or {}).get("name") or line.get("name")
                    if name == "skills":
                        # Skill-description resolver fields: data/skills.py
                        # resolves the ::token:: descriptions from the FULL
                        # row (texts.desc + texts.refs for ::ref_*:: slots,
                        # vars, scalar cooldown/duration, the first step's
                        # range — plus texts.rankDescs and props.rankOverride
                        # for rank-aware text). The loose skill.json is a
                        # compile input only (not bundled — see
                        # FareverPal.spec), so these fields must ride in the
                        # shim for the item page's weapon-skills section to
                        # resolve descriptions in the frozen build. Pruned
                        # to exactly what the resolver touches (~31 KB
                        # compressed vs 138 KB for the full rows).
                        texts = line.get("texts") or {}
                        t = {}
                        for k in ("name", "desc", "refs", "rankDescs"):
                            if texts.get(k) is not None:
                                t[k] = texts[k]
                        if t:
                            row["texts"] = t
                        # rank resolution: props.rankOverride (per-rank
                        # var/prop overrides) + the scalar props templates
                        # read (::charges::, ::duration:: ...)
                        props = line.get("props") or {}
                        p = {}
                        if props.get("rankOverride"):
                            p["rankOverride"] = props["rankOverride"]
                        p.update({k: v for k, v in props.items()
                                  if k != "rankOverride"
                                  and isinstance(v, (int, float))})
                        if p:
                            row["props"] = p
                        if line.get("vars"):
                            row["vars"] = line["vars"]
                        if line.get("cooldown") is not None:
                            row["cooldown"] = line["cooldown"]
                        if line.get("duration") is not None:
                            row["duration"] = line["duration"]
                        # step pruning: keep range + the move step's
                        # duration (data/skills.skill_moves reads them for
                        # the base-chain wind-up / reach lines)
                        steps = [{k: s[k] for k in ("range", "duration")
                                  if s.get(k) is not None}
                                 for s in (line.get("steps") or [])
                                 if s.get("range") is not None
                                 or s.get("duration") is not None]
                        if steps:
                            row["steps"] = steps
                    if name == "units":
                        if not row.get("name"):
                            row["name"] = ut_names.get(line.get("type"), "")
                        
                        # Region assignment: Priority 1: World placement, Priority 2: Dungeon flag, Priority 3: Zone fallback
                        uid = line.get("id")
                        rid = unit_region_map.get(uid)
                        if not rid:
                            if "_D_" in uid or uid.endswith("_D") or uid.startswith("D_") or "Z1D" in uid or "Z2D" in uid or "Z3D" in uid:
                                rid = "Dungeon"
                            elif "Z3" in uid: rid = "Z3"
                            elif "Z2" in uid: rid = "Z2"
                            elif "Z1" in uid: rid = "Z1"
                        row["region"] = rid
                elif name == "zone":
                    zname = line.get("name") or (line.get("texts") or {}).get("name") or KNOWN_REGION_NAMES.get(line.get("id"))
                    row["name"] = zname
                
                # Always resolve gfx/icon for atlas fallback; will be stripped later if using_atlas
                row["gfx"] = line.get("gfx") or (ut_gfx.get(line.get("type")) if name == "units" else None)
                if name == "items" and line.get("icon"):
                    row["icon"] = line.get("icon")
                # Consumables stamp their buff-status ref (props.effects[].
                # status[].ref, e.g. 'Whetstone_Status') so the runtime can
                # map a live buff id back to the item that grants it
                # (data/names.status_name) — the skill sheet carries no
                # source. ~120 extra keys, a few KB in raw_items.
                if name == "items":
                    status_refs = [st.get("ref")
                                   for fx in (line.get("props") or {}).get("effects") or []
                                   for st in fx.get("status") or []
                                   if st.get("ref")]
                    if status_refs:
                        row["statusRefs"] = status_refs
                        # Weightstone's status is authored 'Weighstone_Status'
                        # (game-sheet typo, missing the 't'): stamp the
                        # corrected spelling too so both buff ids resolve.
                        for ref in status_refs:
                            fixed = ref.replace("Weighstone", "Weightstone")
                            if fixed != ref and fixed not in status_refs:
                                row["statusRefs"].append(fixed)

                rows.append(row)
            out[name] = rows
    return out


# Field whitelists: only the fields the runtime actually reads survive
# compilation. world/layer/kind/tile/instances are engine bookkeeping the app
# never consumes (and id is only a dead fallback for chest_locs).
LOC_FIELDS = {
    "chest_locs": ["sub_kind", "chest_id", "world_pos", "z", "lootTable"],
    "poi_locs": ["id", "sub_kind", "world_pos", "z", "name", "zone",
                  "target_activity", "lootTable", "chest_ids",
                  "spawn_unit", "cost_item", "cost_count", "world"],
    "gatherable_locs": ["name", "world", "x", "y", "z"],
    "orb_positions": ["id", "x", "y", "z", "region", "zone", "world"],
    "critter_locs": ["id", "units", "unit"],
    # world mob spawns — the Items page resolves 'unknown location' drop
    # rows against these zones (items/sources.py reads the compiled list)
    "mob_locs": ["unit", "units", "zone"],
}

# --- Instance-row guard ------------------------------------------------------
# The 1-Click extraction started copying res.levels.pak's Level/POI tree into
# the same prefab root the world scanner walks (automated_live_extract.ps1
# "Step 2": `$LevelsPak = res.levels.pak` … "Copy all extracted prefab files
# and subdirectories directly into prefabs/"), so the sheets gained the scenery
# placed INSIDE every dungeon & rift level. On the 2026-09-11 data that took
# dungeon/rift POIs from 14 entrances to 243 (DungeonExit_*,
# *_CheckpointZone_*, Rift_Gate_*, Monolith_*), world orbs from 284 to 324
# (Beehive / KoboldsMines RedOrbs) and added 18 instance ore nodes. Those rows
# carry instance-local coordinates that overlap the overworld map, so packing
# them plastered the minimap with dungeon/rift/orb icons and turned 132 instance
# rows into zone-resolution anchors.
#
# A row is an instance row when its `world` prefab names a dungeon/rift level.
# The scanner currently prunes `world` from the POI/orb sheets, so until it
# stops (LOC_FIELDS above keeps it now), fall back to the row's shape.
_INSTANCE_KINDS = ("poi_locs", "orb_positions", "gatherable_locs")
_INSTANCE_WORLD_TOKENS = ("POI_", "Dungeon")


def _is_instance_row(key, r) -> bool:
    if key not in _INSTANCE_KINDS:
        return False
    w = r.get("world")
    if isinstance(w, str) and w:
        return any(t in w for t in _INSTANCE_WORLD_TOKENS)
    if key == "poi_locs":
        # a real entrance names the activity it opens (or a display name)
        return (r.get("sub_kind") in ("dungeon", "rift")
                and not (r.get("target_activity") or r.get("name")))
    if key == "orb_positions":
        # the static index is the overworld `RedOrb_World_*` placements
        return not str(r.get("id") or "").startswith("RedOrb_World")
    return False


def _drop_instance_rows(key, rows):
    """Remove instance-interior rows (mirrors _prune_loc_rows' shape handling)."""
    if isinstance(rows, dict):
        return {k: _drop_instance_rows(key, v) if isinstance(v, list) else v
                for k, v in rows.items()}
    if isinstance(rows, list):
        return [r for r in rows
                if not (isinstance(r, dict) and _is_instance_row(key, r))]
    return rows


def _prune_loc_rows(rows, keep):
    """Keep only whitelisted fields from each loc row (lists, or dicts that
    wrap lists like orb_positions: {'orbs': [...]})."""
    if isinstance(rows, dict):
        return {k: _prune_loc_rows(v, keep) if isinstance(v, list) else v
                for k, v in rows.items()}
    return [{f: r[f] for f in keep if f in r} for r in rows]


def _compile_loc_datasets(data_new: Path, data_clean: Path) -> dict:
    """Compile the map-location sheets (dungeons, POIs, chests, gatherables,
    orbs, critters, mob spawns), dropping instance-interior rows and narrowing
    each family to LOC_FIELDS. Returns {sheet key: rows}.
    """
    out: dict[str, list] = {}
    for loc_name, key in [
        ("dungeons.json", "dungeons"),
        ("poi_locs.json", "poi_locs"),
        ("chest_locs.json", "chest_locs"),
        ("gatherable_locs.json", "gatherable_locs"),
        ("orb_positions.json", "orb_positions"),
        ("critter_locs.json", "critter_locs"),
        ("mob_locs.json", "mob_locs"),
    ]:
        p = data_new / loc_name
        if not p.exists():
            p = data_clean / loc_name
        if p.exists():
            try:
                jdata = json.loads(p.read_text(encoding="utf-8"))
                rows = (jdata.get(key) or jdata.get("pois")
                        or jdata.get("chests") or jdata.get("gatherables")
                        or jdata.get("critters") or jdata.get("mobs")
                        or jdata)

                rows = _drop_instance_rows(key, rows)

                # Deduplicate poi_locs by appending suffix to duplicate IDs.
                # Two passes:
                #  1. Named-world-NPC rows (sub_kind "npc") are dropped ENTIRELY.
                #     They never render in the app (no NPC minimap layer — only
                #     vendors/petshops show), and their only other use was as
                #     zone-resolution anchors — a value the app doesn't ship
                #     them for. The full dump WITH NPCs stays in the GameFiles
                #     scanner output (Live_Data_Clean) for the website.
                #  2. Exact repeats — the same named node (same kind, id, zone
                #     AND position) double-emitted from overlapping world tiles
                #     is dropped for ANY kind. A second pin at the same spot is
                #     dead weight (this is what the scanner's spatial dedup
                #     cannot catch for vendor/petshop kinds, which it refuses
                #     to merge to protect distinct hub NPCs).
                #  3. Same-id repeats of other kinds get a numeric suffix so
                #     genuinely distinct instances (different positions) stay
                #     addressable.
                if key == "poi_locs" and isinstance(rows, list):
                    seen_spots = set()
                    seen_ids = {}
                    kept_rows = []
                    for row in rows:
                        if row.get("sub_kind") == "npc":
                            continue
                        wp = row.get("world_pos") or {}
                        spot = (row.get("sub_kind"), row.get("id"), row.get("zone"),
                                wp.get("x"), wp.get("y"))
                        if spot in seen_spots:
                            continue
                        seen_spots.add(spot)
                        rid = row.get("id")
                        if not rid:
                            kept_rows.append(row)
                            continue
                        if rid in seen_ids:
                            seen_ids[rid] += 1
                            row["id"] = f"{rid}_{seen_ids[rid]}"
                        else:
                            seen_ids[rid] = 0
                        kept_rows.append(row)
                    rows = kept_rows
                    # The scan names the Guild Merchant NPCs "Wandering Merchant",
                    # but the in-game name is "Guild Merchant" (unit.json
                    # TODO_WanderingMerchant) — normalize so the Vendors layer and
                    # any website show the real name.
                    for row in rows:
                        if is_guild_merchant_source(row.get("id")) \
                                and row.get("name") == "Wandering Merchant":
                            row["name"] = "Guild Merchant"
                
                if key in LOC_FIELDS:
                    rows = _prune_loc_rows(rows, LOC_FIELDS[key])
                
                out[key] = rows
            except Exception:
                pass
    return out


def compile_to_py(raw_data_path: Path, manifest_path: Path, output_file: Path,
                  dev_dir: Path | None = None):
    """
    Reads raw game JSON files from the Unpacked folder and processed manifests
    from assets/data, converting them into a single clean Python script.
    """
    data_new = raw_data_path / "assets" / "data"
    data_clean = manifest_path / "assets" / "data"

    # Fail fast instead of writing near-empty shims: without the extracted
    # game sheets there is nothing to compile, and verify_assets.py would only
    # complain later with a confusing message.
    if not data_clean.exists() or not any(data_clean.glob("*.json")):
        raise SystemExit(
            f"[!] Missing game data: {data_clean} does not exist or has no .json sheets.\n"
            "    Extract the game's JSON files into assets/data, then run\n"
            "    Update_Raw_Data.bat to regenerate the embedded raw_*.py shims.\n"
            "    (build-mod.bat calls the compiler automatically once the data is in place.)"
        )

    all_data = {}

    atlas_dir = data_new.parent / "atlas"
    using_atlas = atlas_dir.exists() and any(atlas_dir.glob("atlas_*.json"))

    # Load mob and critter locations ONLY for region mapping
    unit_region_map = {}
    for fname, key in [("mob_locs.json", "mobs"), ("critter_locs.json", "critters")]:
        p = data_new / fname
        if p.exists():
            try:
                ml_data = json.loads(p.read_text(encoding="utf-8"))
                for entry in ml_data.get(key, []):
                    uids = entry.get("units", [])
                    if entry.get("unit"):
                        uids.append(entry.get("unit"))
                    
                    rid = entry.get("region")
                    zone = entry.get("zone", "") or ""
                    if "CrimsonIsland" in zone or "Ramburg" in zone:
                        rid = "Z3"
                    elif any(k in zone for k in ("Azuram", "Nescent", "Krisomal", "Eksod")):
                        rid = "Z2"
                    
                    if rid:
                        for uid in uids:
                            if uid and uid not in unit_region_map:
                                unit_region_map[uid] = rid
            except Exception:
                pass

    # 1b. Achievement rewards (ach.json): mounts/gliders awarded by achievements
    # (Bestiary/Collector/Savior of <region>, Secret Orb Hunter, collection
    # milestones) instead of world drops. The reward map is stamped onto the
    # matching codex entries below so the runtime can show "Achievement · <name>"
    # as the source for otherwise sourceless (kind: unreleased) items.
    #
    # The raw ach.json id (e.g. 'CompleteDungeon_Z1_All') is compile-time
    # breadcrumbs only — it is intentionally NOT stamped into the payload (the
    # runtime only needs the resolved name). If you ever need to trace a reward
    # back to its raw id, see docs/ACHIEVEMENT_SOURCES.md for the full mapping.
    ach_rewards = {}
    for ap in (data_new / "ach.json", data_clean / "ach.json"):
        if not ap.exists():
            continue
        try:
            ach_raw = json.loads(ap.read_text(encoding="utf-8"))
        except Exception:
            continue
        # Zone display names resolve [Z1_Region] -> "Skover Island" etc.
        zone_names = {}
        for zp in (data_new / "zone.json", data_clean / "zone.json"):
            if not zp.exists():
                continue
            try:
                zraw = json.loads(zp.read_text(encoding="utf-8"))
            except Exception:
                continue
            for zr in zraw.get("lines", []):
                zid = zr.get("id")
                if not zid:
                    continue
                znm = zr.get("name") or (zr.get("texts") or {}).get("name")
                if znm:
                    zone_names[zid.lower()] = znm
            break

        def _ach_name(raw_name):
            if not raw_name:
                return raw_name
            def _repl(m):
                tok = m.group(1)
                if tok in KNOWN_REGION_NAMES:
                    return KNOWN_REGION_NAMES[tok]
                znm = zone_names.get(tok.lower())
                if znm:
                    return znm
                return re.sub(r"(?i)^[ZWR]\d+_", "", tok).replace("_", " ") or tok
            return re.sub(r"\[([^\]]+)\]", _repl, raw_name)

        def _fallback_ach_name(ach_id):
            """Label for achievements with no display name (collection-count
            milestones like CollectMounts_10) -> 'Collect Mounts 10'."""
            if not ach_id:
                return ""
            s = ach_id.replace("_", " ")
            s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", s)
            return re.sub(r"\s+", " ", s).strip()

        for arow in ach_raw.get("lines", []):
            rw = arow.get("reward") or {}
            items = rw.get("items") or []
            if not items:
                continue
            anm = _ach_name(arow.get("name")) or _fallback_ach_name(arow.get("id"))
            if not anm:
                continue
            # desc resolves the same [Z1_Region] placeholders as the name
            # ('Complete all the Dungeons in Skover Island.')
            adesc = _ach_name(arow.get("desc")) if arow.get("desc") else ""
            if "::targetValue::" in adesc:
                # game template token -> the milestone count in the id
                # ('CollectMounts_10' -> 'Collect 10 Mounts.')
                m = re.search(r"(\d+)$", arow.get("id") or "")
                adesc = adesc.replace("::targetValue::", m.group(1) if m else "")
                adesc = re.sub(r"\s+", " ", adesc).strip()
            info = {"name": anm}
            if adesc:
                info["desc"] = adesc
            # Category ids come from ach.json as e.g. 'Exploration_Z3' — collapse
            # the per-region suffix so the tooltip shows 'Exploration' (matching
            # the plain 'Exploration' category) instead of the raw internal id.
            if arow.get("category"):
                info["category"] = re.sub(r"(?i)_Z\d+$", "", str(arow["category"]))
            if arow.get("points") is not None:
                info["points"] = arow["points"]
            for it in items:
                iid = it.get("item") or it.get("id")
                if iid and iid not in ach_rewards:
                    ach_rewards[iid] = info
        break

    # 1. Standard Sheets (Optimized: names resolved and texts/gfx stripped)
    all_data.update(_compile_standard_sheets(data_new, data_clean, unit_region_map))

    # 2. Loot Tables (Nested)
    lt_p = data_new / "lootTable.json"
    if lt_p.exists():
        raw = json.loads(lt_p.read_text(encoding="utf-8"))
        lt_clean = []
        for line in raw.get("lines", []):
            loot_entries = []
            for l in line.get("loot", []):
                entry = {"proba": l.get("proba")}
                for f in ["item", "lootTable", "minLvl", "maxLvl", "conds"]:
                    if f in l: entry[f] = l[f]
                loot_entries.append(entry)
            lt_row = {"id": line.get("id"), "loot": loot_entries}
            if line.get("flags"):
                lt_row["flags"] = line["flags"]
            lt_clean.append(lt_row)
        all_data["lootTable"] = lt_clean

    # 3. Manifest Merging (Consolidated for UI display)
    # Merge info from enemies.json, items.json, skills.json into the primary sheets.
    for manifest_name in ["enemies", "items", "skills"]:
        # Map manifest names to sheet names
        sheet_name = "units" if manifest_name == "enemies" else manifest_name
        
        mp = data_clean / f"{manifest_name}.json"
        if not mp.exists():
            continue
            
        manifest_data = json.loads(mp.read_text(encoding="utf-8"))
        manifest_rows = manifest_data if isinstance(manifest_data, list) else manifest_data.get(manifest_name, [])
        
        sheet_rows = all_data.get(sheet_name, [])
        sheet_map = {r["id"]: r for r in sheet_rows}
        
        for m_row in manifest_rows:
            mid = m_row["id"]
            if mid not in sheet_map:
                # If it's missing from the base sheet, add it
                new_row = {"id": mid}
                if "name" in m_row: new_row["name"] = m_row["name"]
                if "type" in m_row: new_row["type"] = m_row["type"]
                sheet_rows.append(new_row)
                sheet_map[mid] = new_row
            
            s_row = sheet_map[mid]
            
            # Merge extra fields
            if manifest_name == "enemies":
                if m_row.get("isBoss"): s_row["isBoss"] = True
                if m_row.get("isElite"): s_row["isElite"] = True
                if m_row.get("type") == "Critter": s_row["isCritter"] = True
            
            # Icons (stripped later if using_atlas)
            if "icon" in m_row:
                s_row["icon"] = m_row["icon"]
            
            # Ensure name is set if manifest has a better one
            if "name" in m_row and not s_row.get("name"):
                s_row["name"] = m_row["name"]

        all_data[sheet_name] = sheet_rows

    # 4. Collection Catalog (Standalone)
    # The old htdocs curated collection_catalog.json is deliberately NOT read:
    # the game dump's file under that name is a lore 'Collection' list the app
    # doesn't use (it belongs to the not-yet-wired website tool). The mounts /
    # gliders / wild companions catalog is therefore always derived from
    # codex.json. {} if the codex is unavailable too.
    # Shop items count as obtainable: they're buyable NOW even though
    # codex.json flags them 'unreleased'. The set is the drops index's
    # shop-only ids, derived exactly as the runtime does, OR the premium id
    # pattern (rules.is_premium_shop_id) — the union codex.is_shop_item applies.
    shop_ids = _compiled_shop_ids(data_new, data_clean)
    manifest = _collection_catalog_from_codex(data_clean, data_new,
                                              all_data.get("items", []),
                                              ach_rewards, shop_ids) or {}
    items_list = manifest.get("items", [])
    if using_atlas:
        # Strip icons from collection catalog
        for item in items_list:
            if "icon" in item: del item["icon"]
    all_data["info_collection_catalog"] = manifest

    # 4b. Map Location datasets compilation (POI, Chesto, Gatherables, Orbs, Dungeons)
    all_data.update(_compile_loc_datasets(data_new, data_clean))

    # 5. Atlas sprite-sheet coordinates (Merged into specific modules)
    enemies_atlas = _load_atlas_map(atlas_dir, "enemies")
    dungeons_atlas = _load_atlas_map(atlas_dir, "dungeons")
    items_atlas = _load_atlas_map(atlas_dir, "items")
    skills_atlas = _load_atlas_map(atlas_dir, "skills")
    collection_atlas = _load_atlas_map(atlas_dir, "collection")
    minimap_atlas = _load_atlas_map(atlas_dir, "minimap")

    # Combine all for a general lookup if needed
    all_atlas = {**enemies_atlas, **dungeons_atlas, **items_atlas, **skills_atlas, **collection_atlas, **minimap_atlas}
    
    # Fallback/Merge with sheet-based gfx if atlas is missing or incomplete
    for sheet_name in ["skills", "items", "units"]:
        sheet_rows = all_data.get(sheet_name, [])
        for entry in sheet_rows:
            aid = entry.get("id")
            if not aid: continue
            
            # If it's in the specific atlas, we're good. If not, try fallback.
            if aid not in all_atlas:
                gfx = entry.get("gfx")
                if isinstance(gfx, dict) and gfx.get("file"):
                    all_atlas[aid] = {
                        "file": gfx.get("file"),
                        "x": gfx.get("x", 0),
                        "y": gfx.get("y", 0),
                        "size": gfx.get("size", 96),
                        "category": "Enemies" if sheet_name == "units" else sheet_name.capitalize()
                    }

    # Write to multiple files to allow lazy loading and reduce memory pressure
    output_dir = output_file.parent
    # Dev-only readable .json copies are opt-in (compiler --dev-copy writes
    # them to tmp_preview/): by default only the embedded shims are written
    # and no tmp_preview/ folder is created. An explicit dev_dir (tests,
    # --dev-copy) still gets the JSON copies via _write_embedded_data.
    
    def write_data_file(filename, data_dict):
        """Write one shim to this run's output dir (see _write_data_file)."""
        _write_data_file(output_dir, filename, data_dict, dev_dir, using_atlas)

    # Split sheets and bundle their specific atlas data
    unit_ids = {u["id"] for u in all_data.get("units", [])}

    # 6. Codex Data Generation (Grouped by type, one-liner format)
    codex_order_p = manifest_path / "assets" / "data" / "codex_order.json"
    if codex_order_p.exists():
        try:
            c_order = json.loads(codex_order_p.read_text(encoding="utf-8"))
            all_coords = {}
            for fname, key in [("mob_locs.json", "mobs"), ("critter_locs.json", "critters")]:
                p = data_new / fname
                if p.exists():
                    ml_data = json.loads(p.read_text(encoding="utf-8"))
                    for entry in ml_data.get(key, []):
                        uids = set(entry.get("units", []))
                        if entry.get("unit"): uids.add(entry.get("unit"))
                        for uid in uids:
                            if not uid: continue
                            if uid not in all_coords: all_coords[uid] = []
                            wp = entry.get("world_pos") or entry
                            if "x" not in wp: continue
                            c = {"x": round(wp["x"], 4), "y": round(wp["y"], 4)}
                            if c not in all_coords[uid]:
                                all_coords[uid].append(c)

            unit_map = {u["id"]: u for u in all_data.get("units", [])}

            name_to_id = {}
            for u in all_data.get("units", []):
                nm = u.get("name", "").lower()
                if not nm: continue
                uid = u["id"]
                # If we have a duplicate name, prefer the one with world coordinates
                if nm in name_to_id:
                    old_id = name_to_id[nm]
                    if not all_coords.get(old_id) and all_coords.get(uid):
                        name_to_id[nm] = uid
                else:
                    name_to_id[nm] = uid

            dungeon_ids = {
                'Nepsilon', 'Reblochonk', 'Crabgantua', 'Ratsar', 'Gatsbee', 'Mokshi', 'Golcano', 'SpongeBlob',
                'MunsterChuck', 'Cleodora', 'RobinHoof', 'Phrixes', 'DemonSuperElite', 'DemonSuperElite_Fairy'
            }
            # Add mobs/bosses from dungeons.json if available
            for d in all_data.get("dungeons", []):
                if d.get("boss_id"): dungeon_ids.add(d["boss_id"])
                for mid in d.get("mobs", []): dungeon_ids.add(mid)

            # (Dungeon/spark badges need no baked icon coordinates: the UI
            # resolves those map-marker sprites by NAME - see
            # components.set_marker. Rows carry the semantic flags only.)

            codex_json_p = data_clean / "codex.json"
            if not codex_json_p.exists():
                codex_json_p = data_new / "codex.json"
            enriched_map = {}
            enriched_dungeons = []
            if codex_json_p.exists():
                try:
                    cj_raw = json.loads(codex_json_p.read_text(encoding="utf-8"))
                    enriched_map = cj_raw.get("codex", {})
                    # Bosses-tab data is cleaned upstream by scan_codex.py (entrance
                    # locations, names, levels, rift flag) — pass it straight through.
                    enriched_dungeons = cj_raw.get("dungeons") or []
                except Exception as e:
                    print(f"Warning: Failed to parse codex.json: {e}")

            codex_data = {}
            for raw_zone, names_list in c_order.items():
                zone = raw_zone.split()[0].split("(")[0].strip()
                zone_mobs = []
                for nm in names_list:
                    if nm in unit_map:
                        uid = nm
                        u = unit_map[uid]
                    else:
                        uid = name_to_id.get(nm.lower())
                        if not uid: continue
                        u = unit_map.get(uid)
                    if not u: continue

                    tp = u.get("type", "Unknown")
                    disp_name = u.get("name") or nm

                    nu = {"id": uid, "name": disp_name, "type": tp}
                    is_boss = u.get("isBoss")
                    if is_boss: nu["is_boss"] = True
                    if u.get("isElite"): nu["is_elite"] = True
                    if u.get("isCritter"): nu["is_critter"] = True

                    # Real Spark & Dungeon detection logic
                    flags = u.get("flags", 0)
                    # A Spark CANDIDATE, not "unique": this bit (0x40) is the
                    # compiler's stand-in for the real Spark bit (0x400000),
                    # which it overlaps without matching. Named once in
                    # unit_flags.py; do not read it as units.py's is_unique.
                    spark_proxy = (flags & SPARK_PROXY_FLAG_BIT) != 0
                    # dungeons.json (curated per-dungeon mob lists) is the only
                    # authority for dungeon membership. The old bit-7 heuristic
                    # wrongly flagged named world mobs (Sparkling variants,
                    # brawlers, patrol dogs, Crimson captains) as dungeon mobs
                    # even though they spawn in the open world.
                    is_dungeon = uid in dungeon_ids or any(k in uid for k in ("_Z1D_", "_Z2D_", "_Z3D_"))
                    # ...except: a unit carrying the Dungeon bit that has NO
                    # open-world spawn at all is instance/event-only (FS/event
                    # variants, soulstone demons, dungeon elites like the Slick
                    # Nepsid FS whale) — mark it dungeon so the UI doesn't
                    # present a coords-less mob as open-world.
                    # Same physical bit units.py calls UNIQUE — the compiler
                    # reuses it as the instance-only fallback. Named once; see
                    # unit_flags.py for why "unique" is the broad reading.
                    if not is_dungeon and (flags & UNIQUE_FLAG_BIT):
                        _cj = enriched_map.get(uid) or {}
                        if uid not in all_coords and not (_cj.get("locations") or _cj.get("coords")):
                            is_dungeon = True

                    EXCLUDE_DUST = {
                        "Crimson_Z3W_GA_U",            # Great Executioner Léon
                        "FaerieBee_Z2W_Champ_E",       # Notorious Bee
                        "FaerieBee_Z2W_GreatMace_U",   # Left Wing
                        "FaerieBee_Z2W_GreatMace_U_2", # Right Wing
                        "Elemental_Z3W_Earth_U",       # Sparkling Sparkle
                    }
                    is_sparkling = "sparkling" in nm.lower()
                    is_critter = u.get("isCritter", False)

                    if (spark_proxy or (is_sparkling and not is_critter)) and uid not in EXCLUDE_DUST:
                        nu["drops_spark"] = True
                    if is_dungeon:
                        nu["is_dungeon"] = True

                    # No per-row atlas_x/atlas_y is baked here: the app
                    # resolves every icon by NAME through the atlas dict
                    # shipped in the raw_* shims below (data/atlas.py), so a
                    # per-row copy of the same rectangle is unread dead weight
                    # - removed 2026-09-27 alongside v0.3.2's
                    # ICON_DUNGEON/ICON_SPARK constants (commit 92959ec), same
                    # never-read history.
                    coords = all_coords.get(uid, [])
                    # Curated codex.json can supply spawn coords the game dump
                    # misses (e.g. a named mob standing next to its sibling).
                    if not coords:
                        cj_entry = enriched_map.get(uid, {})
                        coords = cj_entry.get("locations") or cj_entry.get("coords") or []
                    if coords:
                        nu["coords"] = coords

                    zone_mobs.append({k: v for k, v in nu.items() if v is not False and v is not None and v != "" and v != []})
                codex_data[zone] = zone_mobs

            # --- MOUNTS, GLIDERS, PETS & OTHERS FROM CODEX.JSON ---
            if enriched_map:
                try:
                    mounts_mobs = []
                    gliders_mobs = []
                    others_mobs = []
                    pets_mobs = []
                    seen_pets = set()

                    for uid, item in enriched_map.items():
                        itype = (item.get("type") or "").lower()
                        iregion = item.get("region") or "Z0"
                        nm = item.get("name") or uid

                        # Open-world zone mobs live in the codex_order loop above
                        # (which now merges codex.json coords) — don't re-emit them
                        # into the Others (Z0) catch-all.
                        if iregion.startswith("Z") and iregion != "Z0":
                            continue

                        is_m = itype == "mount" or uid.startswith("Mount_")
                        is_g = itype in ("glider", "gearglider") or uid.startswith("Glider_")

                        entry = {
                            "id": uid,
                            "name": nm,
                            "type": item.get("type") or ("Mount" if is_m else "Glider" if is_g else "Mob"),
                        }
                        if item.get("icon") and item["icon"] != uid:
                            entry["icon"] = item["icon"]
                        if item.get("is_boss"):
                            entry["is_boss"] = True
                        if item.get("is_elite"):
                            entry["is_elite"] = True
                        if item.get("is_critter") or iregion == "Pets":
                            entry["is_critter"] = True
                        if not (is_m or is_g) and iregion != "Pets" and item.get("drops_spark"):
                            entry["drops_spark"] = True
                        if item.get("is_dungeon"):
                            entry["is_dungeon"] = True
                        
                        coords = item.get("locations") or item.get("coords") or all_coords.get(uid, [])
                        if coords:
                            entry["coords"] = coords

                        if item.get("dungeon_name"): entry["dungeon_name"] = item["dungeon_name"]
                        if item.get("dungeon_loc"): entry["dungeon_loc"] = item["dungeon_loc"]
                        if item.get("vendor_npc"): entry["vendor_npc"] = item["vendor_npc"]
                        if item.get("vendor_npcs"): entry["vendor_npcs"] = item["vendor_npcs"]
                        if item.get("chest_id"): entry["chest_id"] = item["chest_id"]
                        if item.get("chest_loc"): entry["chest_loc"] = item["chest_loc"]
                        if item.get("chest_locs"): entry["chest_locs"] = item["chest_locs"]
                        if item.get("drops_from"): entry["drops_from"] = item["drops_from"]
                        if item.get("group"): entry["group"] = item["group"]
                        if item.get("kind"): entry["kind"] = item["kind"]
                        if item.get("lvl") is not None: entry["lvl"] = item["lvl"]
                        if item.get("mob_id"): entry["mob_id"] = item["mob_id"]
                        if item.get("drop_mob"): entry["drop_mob"] = item["drop_mob"]
                        if item.get("mob_name"): entry["mob_name"] = item["mob_name"]

                        clean_entry = {k: v for k, v in entry.items() if v is not False and v is not None and v != "" and v != []}
                        # Achievement-awarded mounts/gliders (ach.json rewards)
                        # carry their source so the codex shows where they come
                        # from instead of a blank unreleased card.
                        if uid in ach_rewards:
                            clean_entry["achievement"] = ach_rewards[uid]
                            # codex.json flags coords-less items as 'unreleased'
                            # (its scanner doesn't know about achievements), but an
                            # achievement reward IS obtainable in-game — reflag it
                            # so the raw payload doesn't lie. World-drop mounts that
                            # ALSO have an achievement keep their real spawns.
                            if not coords:
                                clean_entry["kind"] = "achievement"

                        if is_m:
                            mounts_mobs.append(clean_entry)
                        elif is_g:
                            gliders_mobs.append(clean_entry)
                        elif iregion == "Pets" or itype == "critter" or item.get("is_critter"):
                            if nm.lower() not in seen_pets:
                                seen_pets.add(nm.lower())
                                pets_mobs.append(clean_entry)
                        else:
                            others_mobs.append(clean_entry)
                    
                    if mounts_mobs: codex_data["Mounts"] = mounts_mobs
                    if gliders_mobs: codex_data["Gliders"] = gliders_mobs
                    if pets_mobs: codex_data["Pets"] = pets_mobs
                    if others_mobs: codex_data["Z0"] = others_mobs
                except Exception as e:
                    print(f"Warning: Failed to parse codex.json in compiler: {e}")

            # --- BOSSES / DUNGEONS (cleaned upstream by scan_codex.py) ---
            # The tool bakes entrance locations, names, levels and the rift flag
            # into codex.json["dungeons"]; the compiler only flattens it and merges
            # repo-side metadata (is_elite from enemies.json, coords from mob_locs).
            # Fallback for a stale codex.json: the plain dungeons.json shape (the
            # runtime's POI step then still resolves entrances).
            if not enriched_dungeons:
                enriched_dungeons = [{
                    "id": d.get("id"), "name": d.get("name"),
                    "entrance_zone": d.get("entrance_zone"), "level": d.get("level"),
                    "is_rift": d.get("entrance_zone") == "Rifts",
                    "boss_id": d.get("boss_id"), "boss_name": d.get("boss_name"),
                    "mobs": [{"id": m} for m in d.get("mobs", [])],
                } for d in all_data.get("dungeons", [])]
            bosses_mobs = []
            for d in enriched_dungeons:
                dname = d.get("name")
                if not dname: continue
                is_r = bool(d.get("is_rift"))
                d_loc = d.get("dungeon_loc")

                # Boss
                bid = d.get("boss_id")
                if bid:
                    u = unit_map.get(bid, {})
                    nm = d.get("boss_name") or u.get("name") or bid
                    # Bosses do NOT drop Spark Dust in the game (verified: none of
                    # the spark-dust loot tables are boss tables) — the spark badge
                    # on boss cards was a false positive.
                    b_entry = {
                        "id": bid,
                        "name": nm,
                        "type": dname,
                        "is_boss": True,
                        "level": d.get("level"),
                        "is_dungeon": True
                    }
                    if is_r: b_entry["is_rift"] = True
                    if d_loc:
                        b_entry["dungeon_loc"] = d_loc
                        b_entry["dungeon_name"] = dname
                    coords = all_coords.get(bid, [])
                    if coords: b_entry["coords"] = coords
                    bosses_mobs.append({k: v for k, v in b_entry.items() if v is not False and v is not None and v != "" and v != []})

                # Mobs
                for m in d.get("mobs") or []:
                    mid = m.get("id") if isinstance(m, dict) else m
                    u = unit_map.get(mid, {})
                    nm = (m.get("name") if isinstance(m, dict) else None) or u.get("name") or mid
                    m_entry = {
                        "id": mid,
                        "name": nm,
                        "type": dname,
                        "level": d.get("level"),
                        "is_dungeon": True
                    }
                    if u.get("isElite"): m_entry["is_elite"] = True
                    if is_r: m_entry["is_rift"] = True
                    if d_loc:
                        m_entry["dungeon_loc"] = d_loc
                        m_entry["dungeon_name"] = dname
                    coords = all_coords.get(mid, [])
                    if coords: m_entry["coords"] = coords
                    bosses_mobs.append({k: v for k, v in m_entry.items() if v is not False and v is not None and v != "" and v != []})

            codex_data["Bosses"] = bosses_mobs

            _write_embedded_data(output_dir, "raw_codex", codex_data, dev_dir)
        except Exception as e:
            print(f"Error compiling codex: {e}")
    else:
        print(f"ERROR: Missing required sequence file: {codex_order_p}")
        print("Run extract_completion_flags.py or Update_Raw_Data.bat to generate assets/data/codex_order.json before compiling.")

    # Crafting: the craft.json recipe sheet + job.json professions, compiled
    # into raw_craft.py so the Craft page works in the frozen build too
    # (these are dev dumps, not .pak sheets, so they aren't part of the
    # raw_data payload). Consumers prefer the shim and fall back to the
    # loose JSONs, matching the other raw_* accessors.
    craft_p = data_new / "craft.json"
    if not craft_p.exists():
        craft_p = data_clean / "craft.json"
    job_p = data_new / "job.json"
    if not job_p.exists():
        job_p = data_clean / "job.json"
    if craft_p.exists() and job_p.exists():
        try:
            _write_embedded_data(output_dir, "raw_craft", {
                "recipes": json.loads(craft_p.read_text(encoding="utf-8"))
                              .get("lines", []),
                "jobs": json.loads(job_p.read_text(encoding="utf-8"))
                            .get("lines", []),
            }, dev_dir)
        except Exception as e:
            print(f"Warning: failed to compile craft.json / job.json: {e}")
    else:
        print("Warning: craft.json / job.json missing — raw_craft.py not written")

    # Item drops: the scan-produced item_drops.json index (item -> drop
    # sources + metadata), compiled into raw_item_drops.py so the Items page
    # works in the frozen build like the other sheets.
    #
    # The scan only recorded CLASS aptitudes in each row's `classes`, which
    # left jewelry (rings / necks / trinkets — stat aptitudes like Vita,
    # Crit, Fervor, ArPen, MaPen) with an empty list, and it dropped the
    # item's faction for most gear. Both matter to the gear stat math: the
    # aptitudes pick the stat curves, and the faction gates the combat-
    # rating curves (Fighter + Manfish rolls Armor Penetration, Fighter +
    # Kobold rolls Critical, etc. — see scaling.aptitudes). The raw item
    # sheet's per-item aptitudes + faction are stamped onto the drops rows
    # here — the same source the items whitelist above compiles from.
    item_aptitudes: dict[str, list[str]] = {}
    item_factions: dict[str, str] = {}
    item_rarity_stats: dict[str, dict] = {}
    # The weapon skills each item wields, from the raw item sheet's `skills`
    # list ([{skill: 'Axe_Base_Attack'}, ...]) — stamped onto the drops rows
    # like aptitudes/faction so the item page's weapon-skills section can
    # render them in the frozen build (item.json, like skill.json, is a
    # compile input only).
    item_skills: dict[str, list[str]] = {}
    # Crafted gear (zone sets, _Craft variants) carries its OWN fixed level /
    # iLevel in the item sheet — the level the piece is made at, which never
    # scales with the zone or the viewer's level slider. The runtime uses it
    # to show crafted gear at its real stats instead of max-level values.
    item_levels: dict[str, int] = {}
    item_ilevels: dict[str, int] = {}
    # The crafting items' granted stats: the scan stamps `stats` for
    # starter gear / scrolls but left the Elixirs, the cooked Food
    # dishes, and the craftable augments (Outfitter embroideries,
    # Blacksmith plates, Enchanter Magic Formulas) statless, so their
    # flat attribute affixes are carried over here — the same sheet
    # source — giving the Enchants-page cards their +N stat lines.
    item_grant_stats: dict[str, list[dict]] = {}
    # Containers roll a rarity SPAN when their gainItem clamps the roll:
    # `props.gainItem.rarity = {min: "Epic"}` is a FLOOR, not a fixed rarity
    # (the roll can climb to the ladder top), and the ONE box in the game
    # today that has one is the Hero Weapon Cache. The compiled item sheet
    # prunes `props`, so the span is stamped onto the drops row here — the
    # same route as aptitudes/faction/skills — and the runtime reads it
    # instead of a hand-recorded floor. Keyed by item id.
    item_roll_rarity_min: dict[str, str] = {}
    item_roll_rarity_max: dict[str, str] = {}
    # The WHOLE `props.gainItem` of every container, stamped onto its drops row
    # as `gain_item`: the loot table it opens into, the level range it rolls at
    # and how many pieces one open yields, next to the rarity floor above. The
    # item sheet prunes `props`, so this is where those facts survive — the
    # cache contract tests read them from the shim instead of the loose sheet,
    # which is what lets the contract run in CI (no game dump -> no skip).
    # Keyed by item id.
    item_gain: dict[str, dict] = {}
    # the item sheet's affix attributes are game-internal names; the
    # rating ones translate to the fareverdb labels the app speaks
    _ATTR_LABELS = RATING_ATTR_LABELS
    items_p = data_new / "item.json"
    if not items_p.exists():
        items_p = data_clean / "item.json"
    if items_p.exists():
        try:
            iraw = json.loads(items_p.read_text(encoding="utf-8"))
            for r in iraw.get("lines", []):
                iid = r.get("id")
                if not iid:
                    continue
                # the gainItem's rarity clamp, if the box has one (see the
                # item_roll_rarity_* note above)
                gi = (r.get("props") or {}).get("gainItem")
                if isinstance(gi, dict):
                    # the whole gainItem (loot table / levelRange / maxItems /
                    # rarity) — see the item_gain note above
                    if gi.get("lootTable"):
                        item_gain[iid] = gi
                    gr = gi.get("rarity")
                    if isinstance(gr, dict):
                        if gr.get("min"):
                            item_roll_rarity_min[iid] = gr["min"]
                        if gr.get("max"):
                            item_roll_rarity_max[iid] = gr["max"]
                apts = [a.get("ref") if isinstance(a, dict) else str(a)
                        for a in (r.get("aptitudes") or [])]
                if apts:
                    item_aptitudes[iid] = apts
                if r.get("faction"):
                    item_factions[iid] = r["faction"]
                sk = [s.get("skill") if isinstance(s, dict) else s
                      for s in (r.get("skills") or [])]
                sk = [s for s in sk if s]
                if sk:
                    item_skills[iid] = sk
                if r.get("level") is not None:
                    item_levels[iid] = r["level"]
                if r.get("iLevel") is not None:
                    item_ilevels[iid] = r["iLevel"]
                # per-rarity stat values — the forward hook for a later game
                # update that ships gear stats at each rarity. item.json has
                # no such column today, so nothing is stamped and
                # gear_rarity_tiers stays single-column; when the data
                # arrives this {rarity: rows} map is passed through and the
                # BY RARITY expansion comes back automatically.
                rs = r.get("rarityStats") or r.get("rarity_stats")
                if isinstance(rs, dict) and rs:
                    item_rarity_stats[iid] = rs
                if (r.get("type") or "") in (
                        "Elixir", "Food", "AugmentOutfitter",
                        "AugmentBlacksmith", "AugmentEnchantHands",
                        "AugmentEnchantFeet", "AugmentEnchantWeapon"):
                    st = [{"n": _ATTR_LABELS.get(
                               (a.get("target") or {}).get("attribute"),
                               (a.get("target") or {}).get("attribute")),
                           "v": a.get("val", 0)}
                          for a in (r.get("affixes") or [])
                          if a.get("ref") == "TAttribute_Flat"
                          and (a.get("target") or {}).get("attribute")]
                    if st:
                        item_grant_stats[iid] = st
        except Exception as e:
            print(f"Warning: failed to read item.json aptitudes/faction: {e}")
    drops_p = data_new / "item_drops.json"
    if not drops_p.exists():
        drops_p = data_clean / "item_drops.json"
    if drops_p.exists():
        try:
            drops_data = json.loads(drops_p.read_text(encoding="utf-8"))
            if drops_data.get("items"):
                for iid, it in drops_data["items"].items():
                    apts = item_aptitudes.get(iid)
                    if apts and not it.get("aptitudes"):
                        it["aptitudes"] = apts
                    fac = item_factions.get(iid)
                    if fac and not it.get("faction"):
                        it["faction"] = fac
                    sk = item_skills.get(iid)
                    if sk and not it.get("skills"):
                        it["skills"] = sk
                    rs = item_rarity_stats.get(iid)
                    if rs and not it.get("rarity_stats"):
                        it["rarity_stats"] = rs
                    lvl = item_levels.get(iid)
                    if lvl and not it.get("level"):
                        it["level"] = lvl
                    il = item_ilevels.get(iid)
                    if il and not it.get("iLevel"):
                        it["iLevel"] = il
                    st = item_grant_stats.get(iid)
                    if st and not it.get("stats"):
                        it["stats"] = st
                    rmin = item_roll_rarity_min.get(iid)
                    if rmin and not it.get("roll_rarity_min"):
                        it["roll_rarity_min"] = rmin
                    rmax = item_roll_rarity_max.get(iid)
                    if rmax and not it.get("roll_rarity_max"):
                        it["roll_rarity_max"] = rmax
                    gi = item_gain.get(iid)
                    if gi and not it.get("gain_item"):
                        it["gain_item"] = gi
                # the scan never captured the boss -> `<boss>_HM` edge; stamp
                # it from the lootTable sheet so no heroic set piece ships
                # blank (see _stamp_heroic_boss_edges)
                _stamp_heroic_boss_edges(drops_data, all_data.get("lootTable"))
                _write_embedded_data(output_dir, "raw_item_drops",
                                     drops_data, dev_dir)
        except Exception as e:
            print(f"Warning: failed to compile item_drops.json: {e}")
    else:
        print("Warning: item_drops.json missing — raw_item_drops.py not written")

    # Location data: the scan-produced world POIs, mob spawns, chests, gatherables, critters, orbs
    locs_data = {}
    for loc_key, loc_name in [
        ("poi_locs", "poi_locs.json"),
        ("mob_locs", "mob_locs.json"),
        ("chest_locs", "chest_locs.json"),
        ("gatherable_locs", "gatherable_locs.json"),
        ("critter_locs", "critter_locs.json"),
        ("orb_positions", "orb_positions.json"),
    ]:
        lp = data_new / loc_name
        if not lp.exists():
            lp = data_clean / loc_name
        if lp.exists():
            try:
                parsed = json.loads(lp.read_text(encoding="utf-8"))
                locs_data[loc_key] = _drop_instance_rows(loc_key, parsed)
            except Exception as e:
                print(f"Warning: failed to load {loc_name}: {e}")
    if locs_data:
        _write_embedded_data(output_dir, "raw_locs", locs_data, dev_dir)

    write_data_file("raw_units.py", {
        "units": all_data.pop("units", []),
        "lootTable": all_data.pop("lootTable", []),
        "atlas": {k: v for k, v in all_atlas.items() if k in unit_ids or k in enemies_atlas or k in dungeons_atlas}
    })
    write_data_file("raw_items.py", {
        "items": all_data.pop("items", []),
        "atlas": {k: v for k, v in all_atlas.items() if k in items_atlas}
    })

    # Whole skill rows, VERBATIM from the source sheet (not the trimmed
    # `skills` mirror above): the item page's "Weapon Upgraded" ladders read
    # the row's `affixes` (8 weapon types), `props.rankOverride` + `vars` (the
    # other 12) and the description templates, and the frozen app has no loose
    # skill.json to fall back on. Trimming these fields here breaks item
    # cards in the build only — tests/test_item_drops.py
    # ::test_weapon_upgrade_ladders_survive_the_frozen_build guards it.
    skill_rows = []
    sk_raw_p = data_new / "skill.json"
    if not sk_raw_p.exists():
        sk_raw_p = data_clean / "skill.json"
    if sk_raw_p.exists():
        try:
            sk_raw_data = json.loads(sk_raw_p.read_text(encoding="utf-8"))
            skill_rows = sk_raw_data.get("lines", []) if isinstance(sk_raw_data, dict) else sk_raw_data
        except Exception:
            pass

    write_data_file("raw_skills.py", {
        "skills": all_data.pop("skills", []),
        "skill_rows": skill_rows,
        "atlas": {k: v for k, v in all_atlas.items() if k in skills_atlas}
    })
    
    # Core data: contains "Misc" atlas data (minimap, collections, etc.)
    misc_atlas = {k: v for k, v in all_atlas.items() if k in collection_atlas or k in minimap_atlas or k in dungeons_atlas or v.get("category") == "misc"}
    all_data["ATLAS_DATA"] = misc_atlas
    write_data_file("raw_data.py", all_data)

def prune_unused_jsons(data_dir: Path):
    """Remove JSON files from assets/data that are not used by the app.

    `required` must name EVERY sheet the build reads - missing one lets the
    pruner delete a compile input (it used to eat craft.json / job.json /
    item_drops.json / codex.json, which verify_assets.py then reported as
    missing), and naming one nothing reads keeps a dead sheet forever
    (codex_completion.json was listed here with no reader at all). The
    scanner-era duplicates - crafting*.json, achievements*.json, map*.json,
    codex_clean / collection_clean / collection_catalog, unitGroup,
    items_manifest, loot_tables, loot_table_contents, codex_completion - are
    deliberately absent so a prune retires them. tests/test_data_layer_sheets.py
    keeps this set in step with the sheets the data layer actually reads.

    `required` must ALSO be a superset of every sheet verify_assets.py requires
    to exist. Update_Raw_Data.bat option [2] prunes AFTER the compile and the
    deletion is permanent (the sheet only comes back from a fresh scan), so
    dropping a verifier-required sheet turns the next build's verify step red
    with the file already gone - that is how item_drops.json / craft.json /
    job.json went missing before. tests/test_data_layer_sheets.py pins the
    superset relation.
    """
    required = {
        "unit.json", "unitType.json", "itemType.json", "rarity.json",
        "zone.json", "item.json", "constant.json", "counter.json",
        "skill.json", "lootTable.json", "enemies.json", "items.json",
        "skills.json", "craft.json", "job.json", "item_drops.json",
        "codex.json", "ach.json", "codex_order.json",
        "dungeons.json", "poi_locs.json", "chest_locs.json",
        "gatherable_locs.json", "orb_positions.json", "critter_locs.json",
        "mob_locs.json", "_version.json",
    }
    
    deleted = 0
    removed = []
    for p in data_dir.glob("*.json"):
        if p.name not in required:
            try:
                p.unlink()
                deleted += 1
                removed.append(p.name)
            except Exception as e:
                print(f"Error deleting {p.name}: {e}")
                
    if deleted:
        print(f"Pruned {deleted} unused JSON files from {data_dir.name}/")
        # Name them: this is a permanent delete (the sheet only comes back from
        # a fresh scan), and the verifier requires some sheets BY NAME - a
        # wrongly-removed one must not scroll by as a bare count.
        print("  removed: " + ", ".join(sorted(removed)))

if __name__ == "__main__":
    import sys
    args = sys.argv
    
    # Handle --prune flag
    if "--prune" in args:
        project_root = Path(__file__).parent
        prune_unused_jsons(project_root / "assets" / "data")
        sys.exit(0)

    # Use provided path or default to project root (flags skipped, so
    # `compiler.py --dev-copy` doesn't mistake the flag for the path)
    positionals = [a for a in args[1:] if not a.startswith("--")]
    raw_source = Path(positionals[0]) if positionals else Path(__file__).parent
    project_root = Path(__file__).parent

    # The shim target comes from THIS file's location plus PACKAGE_DIRNAME, not
    # from the app's own path module: the compiler is standalone and may not
    # import the app tree (it is what generates that tree). PACKAGE_DIRNAME and
    # this arithmetic are pinned by tests/test_compiler.py against the runtime's
    # own `paths.data_root()`.
    package_dir = project_root / PACKAGE_DIRNAME
    out = package_dir / "data" / "raw_data.py"

    dev = project_root / "tmp_preview" if "--dev-copy" in args else None
    compile_to_py(raw_source, project_root, out, dev_dir=dev)
    # Built output, refreshed on every build so a stale copy cannot ship.
    _bundle_shared_rules(package_dir)
