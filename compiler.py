import base64
import json
import re
import zlib
from pathlib import Path


def _embed_shim(stem: str, payload: dict) -> str:
    """Render a self-contained loader shim for `stem`.

    The payload is embedded in the module as zlib+base85-compressed JSON, so
    each shim stays a single self-contained file that loads in one
    decompress + json.load at import time.
    """
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
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


def _write_embedded_data(output_dir: Path, stem: str, payload: dict, dev_dir: Path) -> None:
    """Write {stem}.py (embedded payload) plus {stem}.json as a dev-only copy.
    The readable JSON lands in `dev_dir` (the gitignored tmp_preview/ folder),
    keeping the shipped module dir free of loose plain-text data."""
    payload = dict(payload)
    payload["__meta__"] = _payload_meta()
    p = output_dir / f"{stem}.py"
    p.write_text(_embed_shim(stem, payload), encoding="utf-8")
    dev_dir.mkdir(parents=True, exist_ok=True)
    json_p = dev_dir / f"{stem}.json"
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_p.write_bytes(raw)
    print(f"Written: {p.name} (+{json_p.name} dev copy in {dev_dir.name}/, {len(raw) / 1024:.0f} KB)")

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
# (farever_companion/data/codex.py's _SHOP_ID_RE / is_shop_item). shop.json is
# the canonical list once the dump ships it; until then the id pattern is the
# only signal. Kept in sync with the runtime by test_collection.py's
# cross-check (every catalog shop row must be obtainable).
_SHOP_ID_RE = re.compile(r"(?i)(_ea_|earlyaccess|^spark)")


def _load_shop_ids(data_clean: Path, data_new: Path) -> set[str]:
    """Cash-shop / early-access item ids from shop.json (empty set until the
    dump ships it — callers fall back to _SHOP_ID_RE)."""
    shop_p = data_new / "shop.json"
    if not shop_p.exists():
        shop_p = data_clean / "shop.json"
    if not shop_p.exists():
        return set()
    try:
        rows = json.loads(shop_p.read_text(encoding="utf-8"))
        if isinstance(rows, dict):
            # accept the game's native sheet shape ({"lines": [...]}) as well
            # as the plain {"shop": [...]} / {"items": [...]} wrappers
            rows = (rows.get("shop") or rows.get("items")
                    or rows.get("rows") or rows.get("lines") or [])
        if isinstance(rows, list):
            return {str(r.get("id")) for r in rows
                    if isinstance(r, dict) and r.get("id")}
    except Exception:
        pass
    return set()


def _collection_catalog_from_codex(data_clean: Path, data_new: Path,
                                   item_rows: list,
                                   ach_rewards: dict | None = None,
                                   shop_ids: set[str] | None = None) -> dict | None:
    """Derive the collection catalog (mounts / gliders / wild companions) from
    codex.json. Row ids stay the canonical game ids, so account sync and the
    website keep matching. Returns None when the codex is unavailable, letting
    the caller fall back to {}."""
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
        # is_shop_item (shop.json ids + the premium id pattern) so the
        # catalog agrees with the Codex Collection views instead of listing
        # them [unreleased].
        is_ach_reward = bool(ach_rewards and eid in ach_rewards)
        is_shop = eid in (shop_ids or ()) or bool(_SHOP_ID_RE.search(eid))
        row = {
            "id": eid,
            "name": entry.get("name") or eid,
            "category": cat,
            "subtype": subtype or cat[:-1].capitalize(),
            "obtainable": (entry.get("kind") or "").lower() != "unreleased"
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

    # 1. Standard Sheets (Optimized: names resolved and texts/gfx stripped)
    sheets = {
        "units": ["id", "type", "lvl", "maxLvl", "faction", "flags"],
        "unitType": ["id", "name", "lootTable"],
        # props.generationChance feeds the loot predictor's rarity roll odds
        "rarity": ["id", "color", "props"],
        "zone": ["id", "name"],
        "items": ["id", "rarity", "type"],
        "skills": ["id", "type", "nature"]
    }

    # Hardcoded fallback region names
    KNOWN_REGION_NAMES = {
        "Z1_Region": "Skover Island",
        "Z2_Region": "Valley of Eternal Autumn",
        "Z3_Region": "Crimson Island",
        "CrimsonIsland_Region": "Crimson Island"
    }

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

    # Pre-map unitType names for faster lookup
    ut_p = data_new / "unitType.json"
    ut_names = {}
    ut_gfx = {}
    if ut_p.exists():
        ut_raw = json.loads(ut_p.read_text(encoding="utf-8"))
        for r in ut_raw.get("lines", []):
            if r.get("name"): ut_names[r["id"]] = r["name"]
            if r.get("gfx"): ut_gfx[r["id"]] = r["gfx"]

    # Pre-calculate named bosses (units whose ID matches a loot table ID)
    named_boss_ids = set()
    lt_p = data_new / "lootTable.json"
    if lt_p.exists():
        try:
            lt_raw = json.loads(lt_p.read_text(encoding="utf-8"))
            named_boss_ids = {r["id"] for r in lt_raw.get("lines", [])}
        except: pass

    for name, fields in sheets.items():
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
                # Skip compiling units flagged with NoCodex (bit 18 / 0x40000)
                if name == "units" and isinstance(fl, int) and (fl & 0x40000):
                    continue
                
                # Aggressive Pruning: Skip non-boss/non-unique internal unit types
                if name == "units":
                    utype = line.get("type")
                    is_special = (isinstance(fl, int) and (fl & 0x90)) or (uid in named_boss_ids)
                    if utype in ("Totem", "Environment", "Trigger", "Marker") and not is_special:
                        continue

                # Only include fields that have a value
                row = {f: line[f] for f in fields if line.get(f) is not None}
                
                # Bake the name directly into the row to save runtime logic
                if name in ["units", "items", "skills"]:
                    row["name"] = (line.get("texts") or {}).get("name") or line.get("name")
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

                rows.append(row)
            all_data[name] = rows

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
    # Cash-shop ids are passed separately so shop items count as obtainable
    # (they're buyable NOW even though codex.json flags them 'unreleased').
    shop_ids = _load_shop_ids(data_clean, data_new)
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
    # Field whitelists: only the fields the runtime actually reads survive
    # compilation. world/layer/kind/tile/instances are engine bookkeeping the
    # app never consumes (and id is only a dead fallback for chest_locs).
    LOC_FIELDS = {
        "chest_locs": ["sub_kind", "chest_id", "world_pos", "z", "lootTable"],
        "poi_locs": ["id", "sub_kind", "world_pos", "z", "name", "zone",
                      "target_activity", "lootTable", "chest_ids"],
        "gatherable_locs": ["name", "world", "x", "y", "z"],
        "orb_positions": ["id", "x", "y", "z", "region", "zone"],
        "critter_locs": ["id", "units", "unit"],
    }

    def prune_loc_rows(rows, keep):
        """Keep only whitelisted fields from each loc row (lists, or dicts that
        wrap lists like orb_positions: {'orbs': [...]})."""
        if isinstance(rows, dict):
            return {k: prune_loc_rows(v, keep) if isinstance(v, list) else v
                    for k, v in rows.items()}
        return [{f: r[f] for f in keep if f in r} for r in rows]

    for loc_name, key in [
        ("dungeons.json", "dungeons"),
        ("poi_locs.json", "poi_locs"),
        ("chest_locs.json", "chest_locs"),
        ("gatherable_locs.json", "gatherable_locs"),
        ("orb_positions.json", "orb_positions"),
        ("critter_locs.json", "critter_locs"),
    ]:
        p = data_new / loc_name
        if not p.exists():
            p = data_clean / loc_name
        if p.exists():
            try:
                jdata = json.loads(p.read_text(encoding="utf-8"))
                rows = jdata.get(key) or jdata.get("pois") or jdata.get("chests") or jdata.get("gatherables") or jdata.get("critters") or jdata
                
                # Deduplicate poi_locs by appending suffix to duplicate IDs
                if key == "poi_locs" and isinstance(rows, list):
                    seen_ids = {}
                    for row in rows:
                        rid = row.get("id")
                        if not rid: continue
                        if rid in seen_ids:
                            seen_ids[rid] += 1
                            row["id"] = f"{rid}_{seen_ids[rid]}"
                        else:
                            seen_ids[rid] = 0
                
                if key in LOC_FIELDS:
                    rows = prune_loc_rows(rows, LOC_FIELDS[key])
                
                all_data[key] = rows
            except Exception:
                pass

    # 5. Atlas sprite-sheet coordinates (Merged into specific modules)
    def load_atlas_map(category):
        for ap in atlas_dir.glob(f"atlas_{category}*.json"):
            if ap.exists():
                try:
                    return json.loads(ap.read_text(encoding="utf-8"))
                except: pass
        return {}

    enemies_atlas = load_atlas_map("enemies")
    items_atlas = load_atlas_map("items")
    skills_atlas = load_atlas_map("skills")
    collection_atlas = load_atlas_map("collection")
    minimap_atlas = load_atlas_map("minimap")

    # Combine all for a general lookup if needed
    all_atlas = {**enemies_atlas, **items_atlas, **skills_atlas, **collection_atlas, **minimap_atlas}
    
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
    # Dev-only readable .json copies land in the gitignored tmp_preview/ folder
    # (matched by the `tmp_*` rule), keeping the shipped module dir clean.
    if dev_dir is None:
        dev_dir = Path(__file__).parent / "tmp_preview"
    
    def prune_entries(rows):
        if not using_atlas:
            return rows
        for row in rows:
            row.pop("gfx", None)
            row.pop("icon", None)
        return rows

    # The payload is embedded inside the shim module itself as zlib+base85
    # compressed JSON. That keeps each raw_X.py a single self-contained file
    # and import fast (one zlib.decompress + json.load, ~5x faster than the
    # old Python literal). The shim keeps `from . import raw_X; raw_X.DATA`
    # working everywhere (dev + frozen) without touching any consumer.

    def write_data_file(filename, data_dict):
        stem = Path(filename).stem
        payload = {}
        for key, val in data_dict.items():
            if key in ["units", "items", "skills", "lootTable"]:
                val = prune_entries(val)
            payload[key] = val
        _write_embedded_data(output_dir, stem, payload, dev_dir)

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

            # Metadata Icons from atlas_minimap_01.json
            ICON_DUNGEON = {"x": 492, "y": 2}
            ICON_SPARK = {"x": 1864, "y": 2}

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
                    is_unique = (flags & (1 << 6)) != 0
                    # dungeons.json (curated per-dungeon mob lists) is the only
                    # authority for dungeon membership. The old bit-7 heuristic
                    # wrongly flagged named world mobs (Sparkling variants,
                    # brawlers, patrol dogs, Crimson captains) as dungeon mobs
                    # even though they spawn in the open world.
                    is_dungeon = uid in dungeon_ids or any(k in uid for k in ("_Z1D_", "_Z2D_", "_Z3D_"))

                    EXCLUDE_DUST = {
                        "Crimson_Z3W_GA_U",            # Great Executioner Léon
                        "FaerieBee_Z2W_Champ_E",       # Notorious Bee
                        "FaerieBee_Z2W_GreatMace_U",   # Left Wing
                        "FaerieBee_Z2W_GreatMace_U_2", # Right Wing
                        "Elemental_Z3W_Earth_U",       # Sparkling Sparkle
                    }
                    is_sparkling = "sparkling" in nm.lower()
                    is_critter = u.get("isCritter", False)

                    if (is_unique or (is_sparkling and not is_critter)) and uid not in EXCLUDE_DUST:
                        nu["drops_spark"] = True
                    if is_dungeon:
                        nu["is_dungeon"] = True

                    at = all_atlas.get(uid)
                    if at:
                        if at.get("x"): nu["atlas_x"] = at["x"]
                        if at.get("y"): nu["atlas_y"] = at["y"]
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

    # Shop catalog: the game's shop.json (cash-shop / early-access items),
    # compiled into raw_shop.py so the codex Shop filter lists every entry.
    # Skipped (id-pattern fallback used) until the dump ships shop.json.
    shop_p = data_new / "shop.json"
    if not shop_p.exists():
        shop_p = data_clean / "shop.json"
    if shop_p.exists():
        try:
            shop_rows = json.loads(shop_p.read_text(encoding="utf-8"))
            if isinstance(shop_rows, dict):
                # game-native sheet shape ({"lines": [...]}) plus the plain
                # {"shop": [...]} / {"items": [...]} wrappers
                shop_rows = (shop_rows.get("shop") or shop_rows.get("items")
                             or shop_rows.get("rows") or shop_rows.get("lines") or [])
            if isinstance(shop_rows, list) and shop_rows:
                _write_embedded_data(output_dir, "raw_shop", {"shop": shop_rows}, dev_dir)
        except Exception as e:
            print(f"Warning: failed to compile shop.json: {e}")

    write_data_file("raw_units.py", {
        "units": all_data.pop("units", []),
        "lootTable": all_data.pop("lootTable", []),
        "atlas": {k: v for k, v in all_atlas.items() if k in unit_ids or k in enemies_atlas}
    })
    write_data_file("raw_items.py", {
        "items": all_data.pop("items", []),
        "atlas": {k: v for k, v in all_atlas.items() if k in items_atlas}
    })
    write_data_file("raw_skills.py", {
        "skills": all_data.pop("skills", []),
        "atlas": {k: v for k, v in all_atlas.items() if k in skills_atlas}
    })
    
    # Core data: only contains "Misc" atlas data (minimap, collections, etc.)
    misc_atlas = {k: v for k, v in all_atlas.items() if k in collection_atlas or k in minimap_atlas or v.get("category") == "misc"}
    all_data["ATLAS_DATA"] = misc_atlas
    write_data_file("raw_data.py", all_data)

def prune_unused_jsons(data_dir: Path):
    """Remove JSON files from assets/data that are not used by the app."""
    required = {
        "unit.json", "unitType.json", "rarity.json", "zone.json", "item.json",
        "skill.json", "lootTable.json", "enemies.json", "items.json", "skills.json",
        "dungeons.json", "poi_locs.json", "chest_locs.json",
        "gatherable_locs.json", "orb_positions.json", "critter_locs.json",
        "mob_locs.json", "_version.json", "ach.json",
        "codex_order.json", "codex_completion.json"
    }
    
    deleted = 0
    for p in data_dir.glob("*.json"):
        if p.name not in required:
            try:
                p.unlink()
                deleted += 1
            except Exception as e:
                print(f"Error deleting {p.name}: {e}")
                
    if deleted:
        print(f"Pruned {deleted} unused JSON files from {data_dir.name}/")

if __name__ == "__main__":
    import sys
    args = sys.argv
    
    # Handle --prune flag
    if "--prune" in args:
        project_root = Path(__file__).parent
        prune_unused_jsons(project_root / "assets" / "data")
        sys.exit(0)

    # Use provided path or default to project root
    raw_source = Path(args[1]) if args[1:] and len(args) > 1 else Path(__file__).parent
    project_root = Path(__file__).parent
    
    out = project_root / "farever_companion" / "data" / "raw_data.py"
    
    compile_to_py(raw_source, project_root, out)
