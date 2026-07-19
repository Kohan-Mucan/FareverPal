import json
from pathlib import Path

def compile_to_py(raw_data_path: Path, manifest_path: Path, output_file: Path):
    """
    Reads raw game JSON files from the Unpacked folder and processed manifests
    from assets/data, converting them into a single clean Python script.
    """
    data_new = raw_data_path / "assets" / "data"
    data_clean = manifest_path / "assets" / "data"

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
        "rarity": ["id", "color"],
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
    cc_p = data_clean / "collection_catalog.json"
    if cc_p.exists():
        manifest = json.loads(cc_p.read_text(encoding="utf-8"))
        if using_atlas:
            # Strip icons from collection catalog
            items_list = manifest.get("items", [])
            for item in items_list:
                if "icon" in item: del item["icon"]
        all_data["info_collection_catalog"] = manifest

    # 4b. Map Location datasets compilation (POI, Chesto, Gatherables, Orbs, Dungeons)
    for loc_name, key in [
        ("dungeons.json", "dungeons"),
        ("poi_locs.json", "poi_locs"),
        ("chest_locs.json", "chest_locs"),
        ("gatherable_locs.json", "gatherable_locs"),
        ("orb_positions.json", "orb_positions"),
    ]:
        p = data_new / loc_name
        if not p.exists():
            p = data_clean / loc_name
        if p.exists():
            try:
                jdata = json.loads(p.read_text(encoding="utf-8"))
                rows = jdata.get(key) or jdata.get("pois") or jdata.get("chests") or jdata.get("gatherables") or jdata
                
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
    
    def prune_entries(rows):
        if not using_atlas:
            return rows
        for row in rows:
            row.pop("gfx", None)
            row.pop("icon", None)
        return rows

    def write_data_file(filename, data_dict):
        p = output_dir / filename
        content = '"""Cleaned game data extracted from .pak files."""\n\n'
        content += "DATA = {\n"
        for key, val in data_dict.items():
            if key in ["units", "items", "skills", "lootTable"]:
                val = prune_entries(val)
            content += f"    {repr(key)}: {repr(val)},\n"
        content += "}\n"
        p.write_text(content, encoding="utf-8")
        print(f"Written: {p}")

    # Split sheets and bundle their specific atlas data
    unit_ids = {u["id"] for u in all_data.get("units", [])}
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
        "collection_catalog.json", "dungeons.json", "poi_locs.json", "chest_locs.json",
        "gatherable_locs.json", "orb_positions.json", "critter_locs.json",
        "mob_locs.json", "_version.json", "ach.json"
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
    
    # We always use project_root as the manifest_path (data_clean) so we don't 
    # lose our manual manifest edits in assets/data
    compile_to_py(raw_source, project_root, out)
    print(f"Consolidated data written to {out}")
