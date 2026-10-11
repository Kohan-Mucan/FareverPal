---
title: Farever Pal - Live Companion Tool for Farever
description: A lightweight, read-only companion tool for Farever. Features live DPS meters, entity overlays, loot predictors, and minimaps. Safe, out-of-process, and stable.
layout: default
---

# Farever Pal

**Farever Pal** is a lightweight, out-of-process companion tool for the game **Farever**. It provides real-time information and overlays without ever modifying the game's memory or code, ensuring a safe and stable experience.

[**Download Latest Release**](https://github.com/Kohan-Mucan/FareverPal/releases) | [**View on GitHub**](https://github.com/Kohan-Mucan/FareverPal)

---

##  Key Features
- 💎 **Entity & Loot Overlay**  
  See nearest enemies and chests with real game icons and distances. View the full predicted drop table (by rarity) for the closest chest or enemy based on your current level.
- ⚔️ **Live DPS Meter**  
  Real-time DPS with a live sparkline graph, tracking damage via per-target HP-diff.
- 🗺️ **POI Minimap**  
  Top-down radar showing chests, secret orbs, gatherables, enemies, obelisks, and dungeon entrances with zoom and panning support. Right-click any collectible to mark it as done.
- 📦 **Offline Loot Predictor**  
  Browse the full predicted drop table for any loot table in the game at any player level — entirely offline, no connection needed.
- 🏃 **Speedrun Timer**  
  Floating stopwatch with auto-start on dungeon entry, auto-stop on boss kill, and per-boss personal best tracking.
- 📡 **Server Diagnostics**  
  Check your ping to Farever's servers and see which server IPs the game is actively connecting to. Useful for setting up a VPN split tunnel — route only the game's traffic through your VPN without affecting anything else.
- 📖 **Bestiary Codex**  
  Browsable enemy catalog organized by region, with search and per-zone enable/disable toggles for the entity overlay.
- 🖱️ **Mouse Snapping**  
  Keeps the cursor inside the game window, preventing it from drifting onto a second monitor and stopping you from accidentally clicking overlays mid-combat. 500ms is fine for most users — lower to 100ms if you have a fast or high-DPI mouse.
- 🫥 **Auto-Hide Overlays**  
  Companion panels automatically hide when you open in-game menus so they never block the game UI, then reappear when you close them. Double-click any overlay's title bar to toggle its border on/off for a cleaner look.

---

## 💻 Requirements
- **OS:** Windows 10/11 (64-bit)
- **Game Mode:** Borderless Windowed or Windowed (for overlays to show on top)
- **Permissions:** You may need to "Allow" the application through Windows Defender or your Antivirus on the first run so it can read the game data.

---

## 🛠️ Quick Start
1. [**Download the latest release**](https://github.com/Kohan-Mucan/FareverPal/releases) and extract the folder.
2. Launch **Farever**.
3. Run `FareverPal.exe`.
4. Use the in-game menu to configure your overlays (Default toggle: `F1`).

---

## 💾 Data & Save Files

All settings and progress are stored in a `moddata/` folder created automatically **next to `FareverPal.exe`** on first run. You can back it up, move it between machines, or delete it to reset everything.

| File | What it stores |
|------|----------------|
| `moddata/settings.json` | Global app settings (overlay positions, toggles, hotkeys, etc.) |
| `moddata/progress_<CharacterName>.json` | Per-character progress — collectibles marked as done, hidden entities |

**Each character gets their own file.** When the tool detects which character you're playing, it automatically loads that character's progress so your "done" collectibles on one character don't bleed into another.

### ⚙️ Toggling Individual Ores & Flowers

Each gatherable type can be shown or hidden individually by editing `moddata/settings.json` (while the app is closed) and setting the corresponding key to `true` or `false`:

```json
"show_lavendula":   true,
"show_madrigold":   true,
"show_zealotus":    true,
"show_ancientthyme": true,
"show_copperore":   true,
"show_tinore":      true,
"show_tungstene":   true
```

Set any entry to `false` to hide that gatherable from both the entity overlay and the minimap.

---

## 🛠️ How it Works
Farever Pal is designed to be **read-only**. It reads the game's memory from a completely separate process.
- **No Writing:** It never changes your game state or items.
- **No Injection:** It does not hook into DirectX or the game's executable.
- **Anti-Crash:** Because it's out-of-process, even if the tool closes, your game remains unaffected.

---

## 📜 Credits
Farever Pal is a fan-made community tool, forked from the original project by **[Popparoni](https://github.com/Popparoni/FareverPal)**. Full credit for the original codebase and concept goes to them.

Farever is developed by its respective creators. For full third-party attributions and licenses, please see the [Credits Page](CREDITS.md).
