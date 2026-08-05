import os
import sys
import json
from pathlib import Path

# ANSI colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"

ROOT_DIR = Path(__file__).parent.parent
data_path = ROOT_DIR / "assets" / "data"

# Core database files that are baked into the embedded raw_data.py shim
logic_sheets = [
    "items", "enemies", "loot_tables", "loot_table_contents", 
    "skills", "items_manifest", "lootTable", "dungeons"
]

# Files that are ALWAYS required at runtime for world markers
essential_location_data = [
    "poi_locs.json", "chest_locs.json", "critter_locs.json",
    "orb_positions.json", "gatherable_locs.json", "_version.json",
    "mob_locs.json"
]

missing_count = 0

def status_text(ok, msg):
    return f"{GREEN}OK{RESET} ({msg})" if ok else f"{RED}MISSING{RESET}"

# 1. Raw Data Check
print(f"\n--- {YELLOW}Core Data System Check{RESET} ---")
raw_data_file = ROOT_DIR / "farever_companion" / "data" / "raw_data.py"
has_raw_data = raw_data_file.exists() and raw_data_file.stat().st_size > 1024

if has_raw_data:
    print(f"System: {GREEN}[RAW_DATA ACTIVE]{RESET} ({raw_data_file.stat().st_size / 1024:.1f} KB)")
    for s in logic_sheets:
        p = data_path / f"{s}.json"
        exists = p.exists()
        size_msg = f"{p.stat().st_size / 1024:.1f} KB" if exists else "N/A"
        status = f"{GREEN}OK{RESET}" if exists else f"{YELLOW}SKIPPED{RESET}"
        print(f"Data  {s:20}: {status:18} ({size_msg:8}) -> {YELLOW}SKIPPED (RAW_DATA ACTIVE){RESET}")
else:
    print(f"System: {RED}[RAW_DATA MISSING]{RESET} (Fallback to JSON)")
    # If raw_data is missing, the logic sheets MUST be present
    for s in logic_sheets:
        p = data_path / f"{s}.json"
        if not p.exists():
            missing_count += 1
            print(f"Data  {s:20}: {RED}MISSING{RESET}")
        else:
            print(f"Data  {s:20}: {status_text(True, f'{p.stat().st_size / 1024:.1f} KB')}")

# 2. Essential Location Data (Included in raw_data.py when active)
print(f"\n--- {YELLOW}World Location Data Check{RESET} ---")
for f in essential_location_data:
    p = data_path / f
    exists = p.exists()
    if has_raw_data:
        size_msg = f"{p.stat().st_size / 1024:.1f} KB" if exists else "N/A"
        status = f"{GREEN}OK{RESET}" if exists else f"{YELLOW}SKIPPED{RESET}"
        print(f"Loc   {f:20}: {status:18} ({size_msg:8}) -> {YELLOW}SKIPPED (RAW_DATA ACTIVE){RESET}")
    else:
        if not exists:
            missing_count += 1
            print(f"Loc   {f:20}: {RED}MISSING{RESET}")
        else:
            print(f"Loc   {f:20}: {status_text(True, f'{p.stat().st_size / 1024:.1f} KB')}")

# 3. Icon System Check
print(f"\n--- {YELLOW}Icon System Check{RESET} ---")
atlas_dir = ROOT_DIR / "assets" / "atlas"
using_atlas = False
if atlas_dir.exists():
    json_files = list(atlas_dir.glob("*.json"))
    # Match icons.py: merge all JSON except index and atlas_map
    atlas_jsons = [f for f in json_files if f.name not in ("atlas_index.json", "atlas_map.json")]
    
    if atlas_jsons:
        total_entries = 0
        for m in atlas_jsons:
            try:
                with open(m, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    total_entries += len(data)
            except Exception:
                continue
        print(f"System: {GREEN}[ATLAS ACTIVE]{RESET} ({len(atlas_jsons)} maps, {total_entries} entries)")
        using_atlas = True
    else:
        print(f"System: {YELLOW}[FALLBACK]{RESET} (No maps in Atlas folder)")
else:
    print(f"System: {YELLOW}[FALLBACK]{RESET} (Atlas folder missing)")

# Individual Icon Folders
icon_paths = [
    (ROOT_DIR / "assets" / "icons" / "Items", "Icons Items"),
    (ROOT_DIR / "assets" / "icons" / "Units", "Icons Units"),
    (ROOT_DIR / "assets" / "icons" / "Skills", "Icons Skills"),
    (ROOT_DIR / "assets" / "map_icons", "Map Icons"),
]
for p, label in icon_paths:
    exists = p.exists()
    if exists:
        icons = list(p.glob('*.webp')) + list(p.glob('*.png')) + list(p.glob('*.svg'))
        count = len(icons)
        status = f"{GREEN}OK{RESET}" if count > 0 else f"{YELLOW}EMPTY{RESET}"
        
        if using_atlas:
            print(f"{label:18}: {status} ({count} files) -> {YELLOW}SKIPPED (ATLAS SYSTEM ACTIVE){RESET}")
        else:
            if count == 0: missing_count += 1
            print(f"{label:18}: {status} ({count} files)")
    else:
        if not using_atlas:
            missing_count += 1
            print(f"{label:18}: {RED}MISSING{RESET}")
        else:
            print(f"{label:18}: {YELLOW}NOT IN REPO{RESET} (Atlas handling it)")

if missing_count > 0:
    print(f"\n{RED}[!] Verification FAILED: {missing_count} critical components missing.{RESET}")
    sys.exit(1)
else:
    print(f"\n{GREEN}[+] Verification SUCCESS: All assets optimized.{RESET}")
    sys.exit(0)
