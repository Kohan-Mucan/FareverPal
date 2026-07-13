# Architecture

How Farever Pal is put together, and how one frame flows from raw process memory
to a number on an overlay. Read this before any non-trivial change; the `core/`
and `combat/` source is the reference for the live model itself.

## The shape of it

Farever is a Heaps-engine game running on the **HashLink VM**. HashLink keeps
full runtime type information on every heap object, which is the entire reason
this tool is feasible: we can read an object's class name, walk its super-chain,
and read its fields straight out of another process's memory.

Farever Pal is **out-of-process and read-only**. It is four layers, bottom to top:

```
  ┌─────────────────────────────────────────────┐
  │  ui/        PySide6 windows + overlays        │   draws snapshots; never reads the process
  ├─────────────────────────────────────────────┤
  │  core/model.py  LiveModel                     │   one immutable snapshot per tick
  │   ├─ core/player.py    locate the player      │
  │   ├─ core/scene.py     walk the scene graph   │   ← reads memory (batched)
  │   ├─ core/attributes.py HP / level            │
  │   ├─ core/damage.py    damage events          │
  │   └─ combat/dps.py     DPS aggregation        │   ← pure logic, no reads
  ├─────────────────────────────────────────────┤
  │  core/hl.py     HashLink reflection           │   class names, strings, arrays from raw ptrs
  │  core/proc.py   uniform read API              │   read / read_many / find_bytes
  ├─────────────────────────────────────────────┤
  │  native/ (Rust, farever_native)               │   ReadProcessMemory, batched reads, AOB scan
  └─────────────────────────────────────────────┘
              data/  (static, CDB-backed, process-free): loot, units, rarity, names, icons
```

Two boundaries are load-bearing:

- **`core/` never imports Qt.** The whole live model is testable and runnable
  headless.
- **`ui/` never reads the process.** Every view reads the latest `Snapshot` from
  `LiveModel`. So the overlay, minimap, and DPS meter are always on the same
  frame and the process is never double-read.

## The layers

### `native/` — the Rust memory reader (`farever_native`)
A small PyO3 extension exposing a `Reader` class. It does the raw Win32 work:
`attach` by process name, `read` / `read_many` (one FFI call for a whole batch of
addresses), `find_bytes` / `find_bytes_in` (a fast value scan), and `regions`. It
releases the GIL across scans and batches so the UI stays smooth. The process
handle is opened **read-only** (`PROCESS_VM_READ | PROCESS_QUERY_INFORMATION`);
there is no write/alloc/protect primitive at all, so the reader physically cannot
modify the game.

### `core/proc.py` — uniform read API
`Proc` wraps either the Rust reader (preferred) or `pymem` (fallback) behind one
interface: `read`, `u64/i32/f64`, `read_many`, `find_bytes`, `find_qword`. The
rest of the app only ever talks to `Proc`, so it doesn't care which backend is
live. `has_scan` is `True` only with the Rust backend (pymem can't scan).

### `core/hl.py` — HashLink reflection
Turns raw pointers into meaning. Given an object address it reads the type
pointer, resolves the class name, walks the super-chain (`is_a`, `ancestors`),
reads HashLink strings, and reads HashLink arrays. Type pointers are stable for
the process lifetime, so class-name lookups are cached. This is what lets
`scene.py` say "this object is an `ent.Foe` subclass" without hardcoding anything
per enemy. `field_offset(type, name)` resolves a field's byte offset by name (via
the HL runtime layout table, searching the super-chain), so reads like the camera
yaw don't hardcode a struct offset; it self-validates and returns `None` if the
layout doesn't match, so a wrong assumption degrades instead of misreading.

### `core/player.py` + `core/appsingleton.py` — locating the player
Fully read-only, no writes, no hooks. The primary path (`appsingleton.py`)
anchors on the game's app singleton: it resolves that type by class name once,
then follows runtime pointers to the local player object. The singleton is stable
for the session, so the player is then re-read each frame with no further
scanning. `PlayerLocator` wraps this and falls back to a pure-read heap scan for
the player type if the anchor can't be resolved. The locator exposes `locate`,
`address`, `read_xyz`, and `read_heading`. The app singleton also exposes the
active gameplay camera (`GameApp.camera`, a `client.BaseCamera`); `LiveModel.
camera_yaw()` reads its `curDirection` (offset resolved by name via
`hl.field_offset`). That orbit yaw follows the mouse even while the player stands
still, so the minimap rotates by where you're looking rather than only the body
heading (`ent.Entity.rotationZ`, which turns only while moving); it falls back to
the heading if the camera yaw can't be read.

### `core/scene.py` — the scene snapshot
From the player pointer, walks to the `GameLayer`, then the unit and element
arrays, and reads every object's class, CDB id, owner, and world position. The
key move is **batching**: one `read_many` pulls a header block for *every* unit at
once, turning hundreds of syscalls into one. Produces `Entity` (units) and
`Element` (interactibles: chests, gatherables, obelisks…) records. An enemy is
anything descending from `ent.Foe` and not owned by a hero.

### `core/attributes.py` / `core/damage.py` — the readable stats
`attributes.py` reads current Health (an f64 at a fixed offset) and unit level.
`damage.py` reads the game's own `ui.comp.DamageDisplay` floating-number objects
to get per-skill damage events (this is the upgrade source for the DPS meter; its
field offsets are calibrated live).

### `combat/dps.py` — the DPS engine
Pure logic, zero process reads, fully unit-tested. Two sources feed one
aggregator: **HP-diff** (watch enemy health drop over time → team/area total) and
**events** (per-skill, from `damage.py`). Maintains a rolling-window DPS,
encounter totals, and per-target/per-skill breakdowns.

### `core/model.py` — `LiveModel`
The conductor. Owns the `Proc`, the locator, the `Scene`, the HP-diff `DpsMeter`,
and two extracted managers it delegates to:
- `core/chest_resolver.py` — `ChestResolver`: resolves a chest id to its loot
  table (cache + boss re-attribution) and merges the static overworld index with
  the live scene's chests. Pure policy, no memory reads.
- `core/damage_source.py` — `DamageSourceManager`: the OPT-IN, experimental
  per-skill `DamageDisplay` reader (its lifecycle, background calibration, poller,
  and its own event meter). HP-diff stays the always-on fallback.

Each tick `LiveModel` produces the data the views need (nearest enemies, nearest
chests + resolved loot tables, combat sample). It's where the invariants live:
bosses are tracked regardless of distance; chest loot tables are resolved
deterministically and cached so they don't flicker. Shared layout offsets live in
one place, `core/constants.py`, imported by every reader.

`LiveModel` is a coordinator, not a dumping ground: a new live feature gets its own
delegate (like `ChestResolver` / `DamageSourceManager`) rather than another method
here.

### `data/` — static, process-free
Everything derived from the game's CastleDB (`.cdb`) tables: the recursive loot
predictor, unit→type→loot-table mapping, rarity promotion, id→name, id→icon.
None of it touches the process, so all of it is unit-testable.

### `ui/` — PySide6
One design system (`theme.py` + shared widgets in `widgets.py`): dark, flat,
**sharp corners**, cyan accent. Overlays subclass `overlay_base.py` (frameless,
translucent, always-on-top, click-through). Every view reads from `LiveModel`.

`control_panel.py` is the main window: the shell only (nav rail, stacked pages,
status strip, settings sync). It delegates every other concern:
- `ui/game_attach.py` — `GameAttachmentController`: the read-only session
  lifecycle (the `Proc` handle, the `LiveModel`, the locate worker, and the 2 s
  auto-attach watcher). It holds no UI; it emits `status` / `log` / `locating` /
  `located_changed` / `model_changed` / `detaching` and the panel reacts.
- `ui/overlay_manager.py` — `OverlayManager`: the overlay windows + their toggle
  cards, plus global opacity/lock. The panel exposes `self.proc` / `self.model` /
  `self.overlays` as properties onto these two, so the page builders are unchanged.
- Each page is a mixin in its own file: `ui/pages/{overlays,entity,combat,loot,
  map_page,log}.py`, plus `ui/account.py`, `ui/friends_page.py`,
  `ui/speedrun_page.py`. `ControlPanel` composes them. One generic
  `ui/workers.py:CallWorker` runs any off-thread API call. `tests/test_ui_layering.py`
  enforces a per-file line budget (so the panel can't reabsorb a concern) and that
  page modules import no memory layer.

**Boundary:** `ui/` never reads the process directly — no `.read()` / `.u64()` /
`.find_bytes()` calls; it only consumes `LiveModel`. Importing `Proc` to *attach*
(the control panel's auto-attach) is fine — that's lifecycle, not a memory read.
Conversely the headless layers (`core/`, `combat/`, `data/`, `geo/`) import no GUI
toolkit at module scope; a layering test enforces this (`data/icons.py` is the one
UI-adjacent data module, and it lazy-imports Qt inside functions).

## One frame, end to end

Say the DPS overlay wants to show current DPS:

1. A timer in the UI asks `LiveModel.sample_combat()` (rate-limited to ~5 Hz).
2. `LiveModel` calls `scene.units(player_addr)`:
   - `player.py` already has the player pointer (`player_addr`).
   - `scene.py` follows the player → `GameLayer` link, then the units array, and
     does **one `read_many`** of all unit header blocks.
   - For each block it parses type ptr, owner, xyz, unit-id, and asks `hl.py` to
     resolve the class and id strings (cached). Out come `Entity` records.
3. For each enemy, `attributes.health()` follows the unit → attributes block to
   read current HP (an f64). (The named offsets all live in `core/constants.py`.)
4. `LiveModel` builds a snapshot of `(addr, unit_id, hp)` and feeds it to
   `DpsMeter.update()`, which diffs HP vs the last tick to derive damage. (If the
   `DamageDisplay` event source is calibrated, it uses that instead for per-skill
   numbers.)
5. The overlay reads `dps.current_dps()` and draws it. The overlay never touched
   the process — it only read the model.

That's the whole pattern: **scan/locate once, batch-read the scene each tick,
turn pointers into meaning via `hl.py`, aggregate in pure logic, draw from the
snapshot.** Every new live feature is a variation on it.

## Why these choices (short version)

- **Out-of-process** so the tool physically cannot freeze or crash the game.
- **Rust for the reads** so a whole scene snapshot is one FFI call and locating
  the player is fast enough to stay fully read-only.
- **Reflection over hardcoding** so class/field lookups survive game patches.
- **A single immutable snapshot per tick** so every view agrees on the frame and
  the process is read once.
- **Logic split out from reads** so loot, rarity, and DPS math are unit-tested
  without the game.
