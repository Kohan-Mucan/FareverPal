# Contributing to Farever Pal

Thanks for wanting to help. This guide gets you from a fresh clone to a running
build and a green test suite, then points you at the deeper docs.

If you're new here, read in this order:
this guide → `docs/ARCHITECTURE.md` → the source for the area you're touching
(`core/` + `combat/` for the live model, `ui/` for the interface).

## The one thing to internalise first

Farever Pal is **read-only and out-of-process**. It reads the game's memory from
a separate process and never writes to the game, automates input, or touches the
network. The only in-game code interface is the optional DPS DLL / proxy /
injector path owned by `core/dps_bridge.py`. That path is still read-only in
intent — it receives game damage data, it does not write to the game state,
automate input, or touch the network. Everything else (memory reads, logic, UI)
stays pure read-only. See `docs/ARCHITECTURE.md` for the full rule set — it's
not negotiable, it's the reason the tool is safe to run.

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

Check every prerequisite at once before a build with `build_tools\doctor.bat`:
it probes the venv, Rust, MSVC and the Windows SDK, prints the exact fix for
each that is missing, and exits non-zero when something is.

## Set up

```bat
:: from the companion\ directory
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m maturin develop --release -m native\Cargo.toml   :: builds the Rust ext (farever_native) into the venv
```

Run the app (needs Farever running to actually attach):

```bat
.venv\Scripts\python.exe -m farever_companion
```

Run the tests (no game needed — do this before every PR):

```bat
.venv\Scripts\python.exe -m pytest -q
```

If you skip the Rust build, the app falls back to `pymem` for basic reads, but the
**player locator** needs the Rust `find_bytes` scan, so memory features won't work
without the extension. Logic/UI work is unaffected.

### Dev / experimental flags (environment)

- `FAREVER_DMG_TYPE=0x…` — skip the slow `DamageDisplay` type-locate by passing
  the known type pointer (per game-process; the app verifies it by class name and
  falls back to scanning if stale).
- `FAREVER_DMG_RESULT_TYPE=0x…` — same, for the `st.skill.DamageResult` class.
  Both pointers are auto-captured to settings once located, so only the first
  launch after a game restart pays the full-process scan (visible ticker).
- `FAREVER_DMG_DEBUG=1` — stderr diagnostics (`[dmg-scan] …`) from the DPS
  damage reader while it locates/maps/polls, for re-calibrating offsets after a
  game patch. Each poll logs how many displays/results/events were read plus the
  per-reason drop counts (`no_res`, `bad_hdr`, `bad_amount`, `bad_skill`), and
  whether the field offsets were resolved by runtime reflection (`refl=y`) vs
  the constants fallback.

Two classes gate the reader: `ui.comp.DamageDisplay` (the floaty) and
`st.skill.DamageResult` (its payload). A display's `dmg` slot is only trusted
when the payload's own hl_type header IS the located `DamageResult` class -
pooled/freed floaties point at reused objects (a live `$DebugDay` ref was
caught), so anything failing that header check counts `bad_hdr` and is never
decoded. Field offsets on the verified result type resolve by runtime
reflection first (constants as fallback); live builds ship `serverSource`
empty, so the caster falls back to the `baseSkill` object's owner
(`st.skill.Skill.owner`, resolved by name). Skill *names* are best-effort - a
header-verified event with an unreadable name chain still decodes (skill `?`,
flagged `bad_skill`), it is never dropped.

If the reader finds displays but decodes zero events, it is not silent: the
Top DPS overlay / Combat & DPS Analysis status line switches to
`SOURCE: decoding displays, 0 events — … (stale offsets?)` with those same
drop counts, and the `FAREVER_DMG_DEBUG` log prints them once a second. That
is the evidence to paste after a game patch - either a field name dodged the
reflection lists (refl=n, add the real name to the `_*_FIELDS` tuples in
`core/damage.py`) or an offset/class name drifted (respond by updating
`FAREVER_DMG_TYPE`/`FAREVER_DMG_RESULT_TYPE`/`constants.py`/the `_*_FIELDS`
order).

The DPS meter (Top DPS overlay + Combat & DPS Analysis page) is fed by the
game's own per-hit damage events. `core/dps_source.py` owns the shared event
ring and routes capture to ONE of two engines that deliberately share no code:
`core/dps_bridge.py` (Options 1 & 2 - the in-game DLL, drop-in proxy or
injected, streaming JSON over UDP) and `core/dps_memory.py` (Option 3 - the
DLL-free scanner, which reads `ui.comp.DamageDisplay` numbers via
`core/damage.py`). Fix DLL/proxy/injector problems in the former, offsets and
floaty decoding in the latter; editing one must never break the other.
`core/group_reader.py` reads the replicated `st.Group` party roster (the read
side of the game's invite-to-group flow) and `core/dps_tracker.py` seeds it
into the name maps, so far-away group members resolve by real name instead of
synthetic `Party_XXXX` rows. See `core/damage.py` for the calibration pattern
and offsets.

Damage totals are NEVER HP-derived - enemy health is only read for the boss
bar and encounter state, and hero HP only for death detection (prev>0 ->
hp<=0 ends the fight for wipe handling). There is no estimate channel:
with no damage events, nothing is recorded.

Pets/summons are re-credited to their owners: `core/dps_tracker.py` maps each
player-owned companion (an ent.Foe whose scene owner resolves to an ent.Hero)
to that hero and merges its events into the owner's parse, tagging the pet's
skills `(pet)` instead of showing a synthetic pet line.

## What data the app expects
for the boss bar and encounter state (see `core/damage.py` for the


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
    scene.py            walk the scene graph -> Entity / Element snapshot (batched);
                        owns the instance gate (dungeon/rift detection)
    attributes.py       HP + level reads (display/boss-bar only for DPS)
    damage_events.py    DamageEvent dataclass (shared input to the engine)
    damage.py           DamageDisplay floaty-number event reader
    group_reader.py     st.Group party-roster probe/reader (real names, leader,
                        solo - any distance)
    dps_source.py       DamageSourceManager: owns the event ring, routes capture
    dps_bridge.py       Options 1 & 2: the in-game DLL (proxy / injector, UDP)
    dps_memory.py       Option 3: the DLL-free memory scanner (offsets live here)
    dps_tracker.py      DPS engine: consumes real events -> CombatSession (no HP-diff)
    model.py            LiveModel: one snapshot/tick, shared by every view
  data/                 static, process-free, CDB-backed (loot, units, rarity, names, icons)
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
   New test files go beside the code they cover (`tests/test_<area>.py`), never as
   throwaway scratch files. If you need a scratch test while iterating, put it in
   `tests/scratch/` and delete it before merge — a lone `test_*.py` with no
   corresponding source change is assumed abandoned and will be flagged in review.
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

- [ ] Read-only / out-of-process invariant preserved: no writes to the game,
      no input automation, no network. The only in-game code interface is the
      optional DPS DLL/proxy/injector path in `core/dps_bridge.py`, and that path
      is still read-only in intent — if you touch it, say so explicitly in the PR.
- [ ] `pytest -q` passes.
- [ ] No abandoned test files: every `tests/test_*.py` is tied to a tracked
      feature, and anything in `tests/scratch/` is deleted before merge.
- [ ] New offsets isolated as `None`-defaulting constants if not live-validated,
      with a comment noting how/when they were (or need to be) calibrated.
- [ ] UI changes follow the design system (dark, flat, sharp corners, shared
      widgets).
- [ ] PR description says what you tested — and whether it needs live testing
      against the game that you couldn't do yourself.
- [ ] No orphan `tests/test_*.py` files left behind: any test that is only a
      scratch pad is either deleted or moved into `tests/scratch/` before merge.

Maintainers run live validation against the game for memory-layer PRs, so it's
fine to submit a memory change you could only verify by reasoning — just say so.

## Abandoned test files

Left-behind scratch tests are the easiest kind of repo clutter to miss because
they look legitimate: they live in `tests/`, they start with `test_`, and pytest
runs them. The rule is simple — a one-off `test_*.py` that isn't tied to an
actual feature is not a test, it's a scratch pad, and it should be deleted or
moved into `tests/scratch/` before merge.

- **Real tests** go next to the code they cover: `tests/test_dps.py`,
  `tests/test_damage.py`, and so on.
- **Scratch tests** go in `tests/scratch/`. Use them while iterating, but treat
  the folder as temporary — reviewer expectation is that it's empty in a merged
  PR.
- When in doubt, delete the scratch file rather than leave it. The code under
  test is already in the repo; if the scratch test was worth keeping it
  probably belongs as a real test somewhere, not on its own.

This rule is mirrored in `AGENTS.md` so humans and agents share one standard:
scratch tests are temporary, kept only in `tests/scratch/` while being worked on,
and removed before the change is merged.

### Optional automated check

A lightweight pre-commit or CI check can flag orphan `tests/test_*.py` files —
that is, test files that exist on disk but are not tied to a tracked feature in
the same change. The intent is not to block all new test files, only to surface
one-off scratch files that were left behind. The first pass can be a warning in
PR review; a hard failure only makes sense once the rule is stable and the team
has had time to clean up any existing orphans.
