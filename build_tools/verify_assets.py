import os
import subprocess
import sys
import json
from pathlib import Path

# ANSI colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
RESET = "\033[0m"

ROOT_DIR = Path(__file__).resolve().parent.parent
# Make build_tools importable both when run as a script
# (python build_tools/verify_assets.py) and from the repo root (pytest).
# NOTE: this folder is named build_tools (NOT "packaging") on purpose — a
# repo-root "packaging" package shadows the real packaging library that
# pip / PyInstaller import (packaging.requirements), breaking every build.
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
from build_tools import launcher_check

data_path = ROOT_DIR / "assets" / "data"

# Core database files that are baked into the embedded raw_data.py shim
logic_sheets = [
    "items", "enemies", "loot_tables", "loot_table_contents", 
    "skills", "items_manifest", "lootTable", "dungeons"
]

# Location sheets: required as compiler inputs (they live inside raw_data.py
# in the normal build) and as the runtime fallback when raw_data.py is absent.
# poi_locs + mob_locs also feed the Items page's unknown-location resolution
# (data/items/sources.py reads them from the shim at runtime).
essential_location_data = [
    "poi_locs.json", "chest_locs.json", "critter_locs.json",
    "orb_positions.json", "gatherable_locs.json", "_version.json",
    "mob_locs.json"
]

# Item / Craft page sheets: compiled into their own shims (raw_item_drops.py /
# raw_craft.py) so the pages work in the frozen build. raw_craft covers both
# craft.json (recipes) and job.json (professions). In fallback mode (no shim)
# the loose JSONs must be present instead.
page_shims = {
    "raw_craft.py": ROOT_DIR / "farever_companion" / "data" / "raw_craft.py",
    "raw_item_drops.py": ROOT_DIR / "farever_companion" / "data" / "raw_item_drops.py",
}
page_sheets = ["craft.json", "job.json", "item_drops.json"]

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

# 1b. Item / Craft Page Shims (raw_craft / raw_item_drops)
print(f"\n--- {YELLOW}Item / Craft Page Data Check{RESET} ---")
for shim_name, shim_path in page_shims.items():
    ok = shim_path.exists() and shim_path.stat().st_size > 1024
    if not ok:
        missing_count += 1
    print(f"Shim  {shim_name:18}: {status_text(ok, f'{shim_path.stat().st_size / 1024:.1f} KB') if ok else 'MISSING'}")
for s in page_sheets:
    p = data_path / s
    exists = p.exists()
    if not exists:
        missing_count += 1
        print(f"Data  {s:20}: {RED}MISSING{RESET}")
    else:
        print(f"Data  {s:20}: {status_text(True, f'{p.stat().st_size / 1024:.1f} KB')} -> {YELLOW}SHIM EMBEDDED{RESET}")

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

# 3b. Toolchain Launcher Check — the venv's console-script shims must actually
# run, or a stale/broken launcher silently skips every tool it fronts (e.g. the
# old distlib shims that exited 1 with no output). Fails the build on broken
# shims instead of letting them pass unnoticed.
print(f"\n--- {YELLOW}Console-Script Launcher Check{RESET} ---")
launcher_failures = []
if os.name != "nt":
    print(f"{YELLOW}SKIPPED{RESET} (launcher probing is Windows-only)")
else:
    scripts_dir = Path(sys.executable).resolve().parent
    exes = sorted(p for p in scripts_dir.glob("*.exe") if p.stem not in ("python", "pythonw"))
    print(f"Probing {len(exes)} launchers in {scripts_dir}")
    # Only pure console tools are executed below; pyside6-* launchers are
    # Qt-native/GUI binaries that open windows when launched, so they get a
    # static check instead. Offscreen stays as a defensive default for the
    # run-probed set (harmless for console tools).
    probe_env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for exe in exes:
        name = exe.stem
        if launcher_check.is_static_only(name):
            wrapped = launcher_check.wrapped_target(name, scripts_dir)
            ok = (exe.stat().st_size > 1024 and wrapped is not None
                  and wrapped.exists())
            if not ok:
                launcher_failures.append(name)
            if ok:
                print(f"Tool  {name:22}: {GREEN}OK{RESET} "
                      f"(static — {wrapped.name} present, not executed)")
            else:
                print(f"Tool  {name:22}: {RED}BROKEN{RESET} "
                      f"(launcher or wrapped binary missing)")
            continue
        proc = subprocess.Popen(
            [str(exe)] + launcher_check.probe_args(name),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Tools that ignore the probe flag read stdin when given no files;
            # an inherited pipe never EOFs, so they'd hang until the timeout.
            stdin=subprocess.DEVNULL,
            env=probe_env,
            # No console window flashes during a build.
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        try:
            out, err = proc.communicate(timeout=launcher_check.TIMEOUT_SECONDS)
            output = (out + err).decode("utf-8", "replace")
            status, detail = launcher_check.classify(name, proc.returncode, output, False)
        except subprocess.TimeoutExpired:
            # The launcher spawns the real tool as a child (pyside6-* wrappers
            # use subprocess.call), so kill the whole tree — otherwise the
            # native child survives as an orphan.
            if os.name == "nt":
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True)
            else:
                proc.kill()
            proc.wait()
            status, detail = launcher_check.classify(name, None, "", True)
        if status == "broken":
            launcher_failures.append(name)
        mark = {"ok": f"{GREEN}OK{RESET}",
                "warn": f"{YELLOW}WARN{RESET}",
                "broken": f"{RED}BROKEN{RESET}"}[status]
        print(f"Tool  {name:22}: {mark} ({detail})")

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

if missing_count > 0 or launcher_failures:
    print(f"\n{RED}[!] Verification FAILED: {missing_count} missing component(s), "
          f"{len(launcher_failures)} broken launcher(s): {', '.join(launcher_failures) or 'none'}.{RESET}")
    sys.exit(1)
else:
    print(f"\n{GREEN}[+] Verification SUCCESS: All assets optimized, all launchers run.{RESET}")
    sys.exit(0)
