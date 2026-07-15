# Contributing to Farever Pal

Thanks for wanting to help. This guide gets you from a fresh clone to a running
build and a green test suite, then points you at the deeper docs.

If you're new here, read in this order:
this guide → `docs/ARCHITECTURE.md` → the source for the area you're touching
(`core/` + `combat/` for the live model, `ui/` for the interface).

## The one thing to internalise first

Farever Pal is **read-only and out-of-process**. It reads the game's memory from
a separate process and never writes to the game, injects code, automates input,
or touches the network. Every contribution must keep that true. See
`docs/ARCHITECTURE.md` for the full rule set — it's not negotiable, it's the
reason the tool is safe to run.

## Prerequisites

| Need | Why | Notes |
|---|---|---|
| **Python 3.12+** (3.14 used in dev) | app + UI | `py -m venv` |
| **Rust toolchain** (stable) | builds the native memory reader | https://rustup.rs |
| **maturin** | compiles the Rust ext into the venv | `pip install maturin` (in `requirements.txt`) |
| Windows 10/11 | the reader uses the Win32 API | the live tool is Windows-only; headless tests are cross-platform |
| A copy of Farever (Steam) | only for *live* testing | not needed for logic/UI work |

You do **not** need the game to work on the data layer, the DPS math, or the UI —
those are headless and unit-tested.

## Set up

```bat
:: from the companion\ directory
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
build.bat                 :: compiles the Rust ext (farever_native) into the venv via maturin
```

Run the app (needs Farever running to actually attach):

```bat
.venv\Scripts\python.exe -m farever_companion
```

Run the tests (no game needed — do this before every PR):

```bat
.venv\Scripts\python.exe -m pytest -q
```

If you skip `build.bat`, the app falls back to `pymem` for basic reads, but the
**player locator** needs the Rust `find_bytes` scan, so memory features won't work
without the extension. Logic/UI work is unaffected.

### Dev / experimental flags (environment)

- `FAREVER_EXPERIMENTAL=1` — exposes in-progress features hidden from release
  builds. Currently the per-skill **Skill Breakdown** panel: it's read-only-honest
  but incomplete (samples the scattered live `DamageDisplay` numbers — ~30%
  coverage under heavy load) and slow to calibrate (~5 min cold type-locate), so
  it's off by default. The exact features (headline DPS, by-enemy, survivability)
  are always on. Gate lives in `config.experimental_enabled()`.
- `FAREVER_DMG_TYPE=0x…` — skip the slow `DamageDisplay` type-locate by passing the
  known type pointer (per game-process; the app verifies it by class name and
  falls back to scanning if stale). Only relevant with `FAREVER_EXPERIMENTAL=1`.

## What data the app expects

The app reuses extracted game data (CDB sheets, icons, names, chest positions)
rather than bundling it. `paths.py` locates these relative to the workspace. If
your clone doesn't include that data:

- The **headless tests** run regardless — they use the logic, not the live data
  files where possible.
- The **offline loot predictor** and icons need the sheet/icon files present; if
  they're absent the app degrades (placeholder icons, empty predictions) rather
  than crashing.
- Point the app at your own local copy of that data if you need the full app
  locally. Don't commit large game assets to the repo.

## Where things live

```
native/                 Rust memory reader (farever_native, PyO3) — the read primitives
farever_companion/
  core/                 process access + live model (NO Qt here)
    proc.py             uniform read API over the Rust reader / pymem
    hl.py               HashLink reflection (class names, super-chains, strings, arrays)
    player.py           locate the local player (pure-read, no writes)
    appsingleton.py     resolve the player via the GameApp singleton (read-only)
    scene.py            walk the scene graph -> Entity / Element snapshot (batched)
    attributes.py       HP + level reads
    damage.py           DamageDisplay event reader (per-skill DPS source)
    model.py            LiveModel: one snapshot/tick, shared by every view
  data/                 static, process-free, CDB-backed (loot, units, rarity, names, icons)
  combat/dps.py         the DPS engine (pure logic, unit-tested)
  geo/                  chest positions + POI model for the minimap
  ui/                   PySide6 only — theme, shared widgets, overlays, control panel
tests/                  headless logic tests (pytest)
docs/                   ARCHITECTURE.md
```

`core/` must not import Qt; `ui/` must not read the process directly — it goes
through `LiveModel`. Keeping that boundary is what lets the logic stay testable.

## Making a change

1. **Branch** off the default branch.
2. Keep changes within one layer where you can. A bug in DPS math is a `combat/`
   change with a `tests/test_dps.py` case; a new tracked stat is a `core/` change.
3. **Add/extend a test** for anything in `data/`, `combat/`, or the pure parts of
   `core/`. These are the parts we *can* test without the game, so we do.
4. For memory offsets you can't verify without the game: isolate the offset as a
   named constant defaulting to `None`, make the feature no-op until calibrated,
   and say in the PR that it needs live calibration. Never fabricate a value. See
   `core/damage.py` and `core/player.py` for the pattern (the `OFF_*: int | None`
   constants).
    - **Known Offset Traps:** Some offsets like `OFF_HERO_OWNERPLAYER` (usually `0x498`) are stable across patches but the Calibrator GUI often misidentifies them as `0x10`. If pets show up as wild companions, `OFF_HERO_OWNERPLAYER` or `OFF_OWNER` (common values: `0x4b8`, `0x78`, `0x60`) are misaligned.
5. `pytest -q` must be green.
6. Match the surrounding style. Sharp-corner dark UI for anything visual (see
   `docs/ARCHITECTURE.md`).

## PR checklist

- [ ] Read-only / out-of-process invariant preserved (no writes, no injection in
      the normal path, no network, no input automation).
- [ ] `pytest -q` passes.
- [ ] New offsets isolated as `None`-defaulting constants if not live-validated,
      with a comment noting how/when they were (or need to be) calibrated.
- [ ] UI changes follow the design system (dark, flat, sharp corners, shared
      widgets).
- [ ] PR description says what you tested — and whether it needs live testing
      against the game that you couldn't do yourself.

Maintainers run live validation against the game for memory-layer PRs, so it's
fine to submit a memory change you could only verify by reasoning — just say so.
