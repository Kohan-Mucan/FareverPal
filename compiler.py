import json
from pathlib import Path

def compile_to_py(raw_data_path: Path, htdocs_path: Path, output_file: Path):
    """
    Reads raw game JSON files from the Unpacked folder and processed manifests
    from htdocs, converting them into a single clean Python script.
    """
    data_new = raw_data_path / "assets" / "data"
    data_clean = htdocs_path / "assets" / "data"

    all_data = {}

    # Detect Atlas system to strip redundant icon data safely
    atlas_src = data_new.parent / "atlas"
    using_atlas = atlas_src.exists() and (atlas_src / "atlas_map.json").exists()

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
        "zone": ["id"]
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

    for name, fields in sheets.items():
        # Mapping plural internal names to singular CDB filenames
        json_name = "unit" if name == "units" else name
        p = data_new / f"{json_name}.json"
        if p.exists():
            raw = json.loads(p.read_text(encoding="utf-8"))
            rows = []
            for line in raw.get("lines", []):
                # Skip compiling units flagged with NoCodex (bit 18 / 0x40000)
                fl = line.get("flags", 0)
                if isinstance(fl, int) and (fl & 0x40000):
                    continue
                # Only include fields that have a value
                row = {f: line[f] for f in fields if line.get(f) is not None}
                # Bake the name directly into the row to save runtime logic
                if name == "units":
                    row["name"] = (line.get("texts") or {}).get("name") or ut_names.get(line.get("type"), "")
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
                    row["name"] = (line.get("texts") or {}).get("name")
                
                # Strip gfx ONLY if using atlas, otherwise fallback to type gfx if needed
                if not using_atlas:
                    row["gfx"] = line.get("gfx") or (ut_gfx.get(line.get("type")) if name == "units" else None)

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

    # 3. Items (Nested name)
    item_p = data_new / "item.json"
    if item_p.exists():
        raw = json.loads(item_p.read_text(encoding="utf-8"))
        all_data["items"] = []
        for line in raw.get("lines", []):
            row = {
                "id": line.get("id"),
                "name": (line.get("texts") or {}).get("name"),
                "rarity": line.get("rarity"),
                "type": line.get("type"),
            }
            if not using_atlas and line.get("icon"):
                row["icon"] = line.get("icon")
            
            # Prune None values to save space
            pruned_row = {k: v for k, v in row.items() if v is not None}
            all_data["items"].append(pruned_row)

    # 4. Manifests (Consolidated for UI display)
    # We load the base manifests from htdocs but override names/icons 
    # using the raw sheet "Game Logic" to ensure they are always correct.
    utypes = {r["id"]: r for r in all_data.get("unitType", [])}

    for info_name in ["enemies", "items", "skills", "collection_catalog"]:
        wp = data_clean / f"{info_name}.json"
        if not wp.exists():
            continue
            
        manifest = json.loads(wp.read_text(encoding="utf-8"))
        rows = manifest if isinstance(manifest, list) else manifest.get(info_name, [])
        
        if info_name == "enemies":
            units_map = {r["id"]: r for r in all_data.get("units", [])}
            clean_rows = []
            for r in rows:
                uid = r["id"]
                u = units_map.get(uid)
                name_val = u.get("name") if u else r.get("name") or uid
                utype = r.get("type")
                clean_row = {
                    "id": uid,
                    "name": name_val,
                    "type": utype,
                }
                if r.get("isBoss"):
                    clean_row["isBoss"] = True
                if r.get("isElite"):
                    clean_row["isElite"] = True
                if utype == "Critter":
                    clean_row["isCritter"] = True

                if not using_atlas and "icon" in r:
                    clean_row["icon"] = r["icon"]
                clean_rows.append(clean_row)
            manifest = clean_rows
        
        elif info_name == "items":
            items_map = {r["id"]: r for r in all_data.get("items", [])}
            clean_rows = []
            for r in rows:
                iid = r["id"]
                item = items_map.get(iid)
                name_val = item.get("name") if (item and item.get("name")) else r.get("name") or iid
                clean_row = {
                    "id": iid,
                    "name": name_val
                }
                clean_rows.append(clean_row)
            manifest = clean_rows

        elif info_name == "skills":
            clean_rows = []
            for r in rows:
                clean_row = {
                    "id": r["id"],
                    "name": r.get("name") or r["id"]
                }
                clean_rows.append(clean_row)
            manifest = clean_rows

        elif info_name == "collection_catalog" and using_atlas:
            # Strip icons from collection catalog as well
            items_list = manifest.get("items", [])
            for item in items_list:
                if "icon" in item: del item["icon"]

        all_data[f"info_{info_name}"] = manifest

    # 5. Atlas sprite-sheet coordinates
    atlas_data = {}
    if using_atlas:
        # Load from the pre-computed atlas map if it exists
        try:
            atlas_data = json.loads((atlas_src / "atlas_map.json").read_text(encoding="utf-8"))
        except Exception:
            pass
    
    # Fallback/Merge with sheet-based gfx if atlas is missing or incomplete
    for sheet_name in ["skills", "items", "units"]:
        # Map plural display names to singular CDB sheet names
        json_name = sheet_name.rstrip('s')
        sp = data_new / f"{json_name}.json"
        if not sp.exists():
            continue
        raw = json.loads(sp.read_text(encoding="utf-8"))
        lines = raw if isinstance(raw, list) else raw.get("lines", [])
        for entry in lines:
            aid = entry.get("id")
            if not aid or aid in atlas_data:
                continue
                
            gfx = entry.get("gfx")
            if not isinstance(gfx, dict) and sheet_name == "units":
                # Fallback to UnitType gfx
                utid = entry.get("type")
                gfx = utypes.get(utid, {}).get("gfx")

            if not isinstance(gfx, dict):
                continue
            gfx_file = gfx.get("file")
            if not gfx_file:
                continue
                
            atlas_data[aid] = {
                "file": gfx_file,
                "x": gfx.get("x", 0),
                "y": gfx.get("y", 0),
                "size": gfx.get("size", 96),
            }
    all_data["ATLAS_DATA"] = atlas_data

    # Write to a single Python file
    content = '"""Cleaned game data extracted from .pak files."""\n\n'
    content += "DATA = {\n"
    for key, val in all_data.items():
        content += f"    {repr(key)}: {repr(val)},\n"
    content += "}\n"

    output_file.write_text(content, encoding="utf-8")

if __name__ == "__main__":
    # Use htdocs as the source for both raw sheets and processed manifests
    htdocs_root = Path(__file__).parent  # reads from FareverPal/assets/data/
    
    out = Path(__file__).parent / "farever_companion" / "data" / "raw_data.py"
    compile_to_py(htdocs_root, htdocs_root, out)
    print(f"Consolidated data written to {out}")
