# Farever Pal

A **read-only**, **out-of-process** live companion tool for Farever
(Heaps / HashLink). It attaches to the running game and locates your character
purely by **reading** memory — no writes, no code injection, no network — and
shows:

- **Entity / loot overlay** — nearest enemies & chests with the real game icons,
  distance, and the closest source's full predicted drop table by rarity.
- **DPS meter** — live DPS, sparkline, per-target (HP-diff) or per-skill/crit
  (via the game's own `DamageDisplay` numbers) breakdown. Self DPS only.
- **Minimap** — top-down POI radar (chests, gatherables, enemies, obelisks),
  zoom, right-click to mark collectibles done.
- **Dungeon Secret Orbs** — Automatic detection of InstanceOrbs and activity orbs
  within dungeons, even those not in the overworld static database.
- **Offline loot predictor** — any loot table at any level.

It is out-of-process and write-free, so it **cannot freeze or crash the game**
(unlike an in-process DirectX-hook overlay).

> Read-only by design. Farever is online & server-authoritative; this tool only
> observes and predicts — it cannot and does not change drops or any game state.
> It reads the game's memory from a separate process and **never writes to the
> game or injects any code.**

## How it works (overview)

The whole bet is that an external reader plus a separate Qt window can surface
live info **without ever touching the game's process state** — so it can't crash,
freeze, or alter the game.

- **Out-of-process, read-only.** A small Rust extension opens the game with a
  read-only handle (no write / alloc / protect access) and exposes only reads and
  a memory scan. There is no write path anywhere in the tree, and a test enforces
  that.
- **Reflection, not hard-coded addresses.** The game runs on HashLink, which keeps
  class and field names in the runtime. The app reads those to identify objects by
  *name* (your character, enemies, chests, …) rather than relying on brittle fixed
  addresses, so it tends to survive game updates.
- **A stable anchor for the player.** Instead of rescanning a multi-GB heap on
  every zone change, it resolves one long-lived game object and follows it to your
  character each frame — so tracking keeps working across loads and menus with no
  repeated scan.
- **Batched scene reads.** Each frame it pulls the live unit / interactible lists
  in as few reads as possible, then interprets them in Python for the overlays.
- **Loot is predicted entirely offline.** Drop tables come from the game's own data
  files (CastleDB); the tool never reads or influences a live roll — drops are
  server-authoritative, which a client cannot change.
- **DPS** comes from watching enemy health fall over time, plus the game's own
  on-screen damage numbers for the per-skill breakdown. Self only.

The tool *reads* derived, public-facing information for a fan wiki and personal
use.

## Layout

```
companion/
  native/            Rust memory reader (farever_native, PyO3)
  farever_companion/ the app (core / data / combat / geo / ui)
  tests/             headless logic tests
  CREDITS.md         third-party attribution
```

Data (CDB sheets, icons, names, chest positions) is stored in the `assets/`
directory and consolidated into the app during build.

## Utility Scripts

The `farever_companion/data/tools/` directory contains helper scripts for
data management and asset extraction.

## Run from source

```bat
:: from companion\  (a .venv with the deps is already set up)
.venv\Scripts\python.exe -m maturin develop --release -m native\Cargo.toml   :: build the Rust extension (farever_native)
.venv\Scripts\python.exe -m farever_companion
```

If `.venv\` is missing, recreate it:

```bat
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

If you don't build the Rust extension, the app falls back to `pymem` for basic
reads, but the **player locate** (memory scan) needs `farever_native`.

## Test

```bat
.venv\Scripts\python.exe -m pytest -q
```

## Build the single-file .exe

```bat
build-mod.bat    :: one-shot pipeline: Rust ext -> compile data shims -> PyInstaller -> verify
```

`build-mod.bat` handles the whole pipeline in one go (Rust `farever_native`,
`compiler.py` embedded shims, PyInstaller, `packaging/verify_assets.py`) and
names the output `dist\FareverPal-<version>-<commit>.exe` from the latest git
commit. The resulting exe (~105 MB, self-contained) is published as a GitHub
**release asset**, not committed to the repo.

## Use

1. Launch Farever and load fully into the world.
2. Run the app — it attaches **automatically** when Farever is running and locates
   your character once you're in-world (no button; the status line shows when it's
   ready and follows you across zones/menus).
3. Open the overlays. Drag them by their title bars; set opacity / click-through
   in the control panel.

## Status

The core overlays (entity/loot, DPS, minimap) and the read-only player locate are
**live-validated**. Per-skill DPS stays behind an experimental flag (incomplete
coverage). Some newer pieces (e.g. speedrun difficulty auto-detect) are still
being refined.

## Credits & third-party work

See [`CREDITS.md`](CREDITS.md) for full attribution. In short:

- **Farever** is made by its developers — this is an unofficial community fan
  tool. Please support the game on Steam. Taken down / stripped on request.
- The minimap's world→pixel coordinate transform is derived from the community
  web map **[farever-map](https://github.com/IceCaveBear/farever-map)** by
  **IceCaveBear**. Credit and thanks to them.
- Built on the **Heaps** engine + **HashLink** VM (the game's stack); bundled
  fonts (Inter, JetBrains Mono) and Lucide icons under their own licenses.
