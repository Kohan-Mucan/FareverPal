# FareverPal Project Rules

Read-only, out-of-process companion for Farever: it reads the game's memory from a
separate process and never writes to the game, automates input, or touches the
network. Safety rules and design system: `docs/ARCHITECTURE.md`. Build setup:
`CONTRIBUTING.md`.

## Start Here — orient before you read

Pick the layer first. Reading out of layer is the usual cause of a wasted session.

| Task looks like | Real layer | Start in |
|---|---|---|
| Wrong/ugly/missing UI, hover, resize, tab wiring | `ui/` | the page mixin (map below) |
| DPS totals, per-skill numbers, sessions, pets, segments | `core/` (+ `data/`) | `core/dps_tracker.py`, `core/damage.py` |
| Overlay behaviour / persistence | `ui/overlays/` + `config/` | `ui/overlay_manager.py`, `config._PROP_MAP` |
| A number is wrong after a game patch | memory offsets | `constants.py` + `core/damage.py` (below) |
| Loot, items, recipes, icons, names | `data/` | `farever_companion/data/` |

## Verify

**One command, no arguments needed:** `.venv\Scripts\python.exe -m pytest -q`

Use the venv interpreter — plain `python` lacks PySide6 and every UI test errors on
import. **It verifies narrowly for you.** With a dirty working tree, `tests/affected.py`
collects only the tests the change can reach — each test module's transitive import
closure, the tests that name a changed file, and the whole-tree contract tests
(`test_repo_hygiene.py` always, `test_ui_utils.py` for anything under `ui/`) — and
skips the rest, so the usual edit is seconds, not the whole suite (~6 min). It fails
open: a `conftest` change, a source module no test reaches, or unreadable git all run
the full suite. CI, an explicit path/nodeid, `-k`/`-m`, and `--scope=all`
(`FAREVER_TEST_SCOPE=all`) mean the full suite too. The header line under `pytest`
says what was selected and how to widen it.

The suite is **green**; a red run means you broke something. Do not loosen an
assertion until you have read the code it describes — `docs/history.md` records which
failures the baseline already fixed, and which "fixes" were really stale tests.

Headless Qt quirk: `processEvents()` alone never delivers `deleteLater` (PySide
6.11.1) — rebuilt widgets stay `findChildren`-visible until
`tests/qt_helpers.drain_deleted()` runs it off. Use it when a test asserts absence
after a rebuild.

## The test suite is part of the spec

`tests/test_repo_hygiene.py` and `tests/test_ui_utils.py` enforce the contracts
easy to break by accident. Two fail *silently* until a release:

- `FareverPal.spec` `hiddenimports` vs `overlay_manager._OVERLAY_SOURCES` — the
  overlays load by runtime string, so that list is the only thing between a frozen
  build and five missing HUDs.
- `test_the_agents_page_table_only_names_modules_that_exist` — a filename in the
  table below that resolves to nothing.

One more contract is not silent, but it is invisible until someone repeats the
mistake: `test_every_hud_surface_is_built_by_exactly_one_window` walks each HUD
window's bases (a mixin's file counts as part of that window's surface) and fails
when two windows each build the same class under `ui/overlays/`, unless it is
declared shared chrome in `_SHARED_HUD_SURFACES`. The Top DPS meter and the Test
Dummy HUD both mounted a dummy test that way — the meter's copy only appeared
when the dummy HUD was off, so the two could stack over one dummy.

The cache table is the same shape of contract, pinned in `tests/test_item_drops.py`
rather than this file's two whole-tree suites. `sources._HEROIC_CACHE_SPECS` is the
app's hand-recorded claim about the rolling boxes it knows; the game's `item.json`
is the truth, and the compiler bakes each container's `props.gainItem` (loot table,
level range, `maxItems`, rarity clamp) onto its drops row as `gain_item`.
`test_compiled_gain_item_facts_are_the_contract_for_every_container` walks every
`LootableContainer` the COMPILED shim declares — never a hand-written id list — and
fails on a box the HAND table never specced unless it is named in
`_UNSPECCED_CONTAINERS`, while its siblings pin each claimed box's loot table, level
range, `maxItems` and rarity floor to that compiled copy. That name list is not an
app-level exclusion: `sources.container_spec()` / `container_contents()` DERIVE a
spec and a Contains list for every container the hand table does not claim (from the
baked `gain_item`), so the Items page surfaces the starter "Mysterious Cache" like
any other box instead of hiding it behind a test-only set. Adding a box to the game, or re-leveling one, is
a red test rather than a stale Items page. (Reading the shim, not the loose sheet,
is what lets these run in CI: the dump is absent there, the shim ships with the app,
so a drift is a red test instead of a skip CI never sees.)

Hand-recorded game facts have a census of their own. A value the code marks
`# HAND-FACT: <name>` — a fact no sheet or compiled shim carries, like the cache
vendors' 100-medal price, a box's prose, or a pool size the game never exposes — is
registered in `tests/test_repo_hygiene.py`'s `HAND_GAME_FACTS` with the test that
pins it. `test_every_hand_recorded_game_fact_names_its_contract_test` is
bidirectional: a registered fact must still carry its marker and a guard that
resolves to a module-level test, and a NEW marker must be registered — so a fresh
hand fact is a red test until it has a contract test or a line in
`_UNGUARDED_HAND_FACTS`, the ratchet that makes the unguarded ones (the WorldLoot pool
sizes, `_SLOT_ORDER`) visible instead of silently trusted. Add the marker when you
record one.

`test_ui_file_under_line_budget` enforces an **1200-line budget on every file under**
**`farever_companion/ui/`**. `DEV_ONLY_FILES` is empty on purpose — the one tool that
ever needed an exemption now lives in `build_tools/dev/`, outside the package, so a
frozen build can't reach it. If you touch a file near the cap, read the shard rule
below before moving anything out of it: a family that needs a second file gets ONE
shard of real size, never a module per widget group. The old splits to copy —
`components.py` 1311→659, `loadout.py` 1355→622, `dungeon_overlay.py` 881→602, each
re-exporting its public names so no call site moves — are those splits' own records,
not today's sizes: most of the fragments went back when the ceiling rose (below).

The budget is a GOD-OBJECT GUARD, never a split target. Raised 840 -> 1200 on
2026-10-06: at 840 it had stopped guarding and started forcing coherent concerns
apart. Every file it pushed a fragment out of was left 650-840 lines, so the
fragments (68-630 lines, ONE consumer each) existed only to hold the number down,
and each one paid for itself with delegating methods, mid-file imports and
re-export shims that pointed back at its own owner. If the cap pushes a fragment
out of a file and that fragment ends up with ONE consumer, fold it back when the
file has room instead of leaving a satellite that imports into its own owner. The
2026-10-06 fold did exactly that: the Run Timer's render + diagnostics halves
rejoined `speedrun_overlay.py` (1063 -> the window is 1038), the overlay manager's
lifecycle + restore mixins rejoined `overlay_manager.py` (1055), the row tag chips
rejoined `skill_row.py` (841) and the rail's width rules rejoined `combat_rail.py`
(753). A second pass over the rest of the v0.3.6 satellites folded each one-consumer
fragment back into the module that imports it: the Past Fights action bar into
`combat_fights_list.py`, the enchants cost table into `enchants.py`, the loadout
totals sidebar into `loadout.py`, the DPS game-folder lookup into `dps_proxy.py`
(its two mixins merged into one) and the small Settings DPS facets into
`dps_engine.py`.

**The shard rule.** Folding is right for a fragment that is its one consumer's own
concern, but a family of six 190-line satellites is not six concerns — it is one
family the budget split. The budget measures ONE file, so a family too big for one
file gets ONE second file carrying the rest of it, at real size: four such pairs
today, each half 357-1147 lines: `combat_fights_list.py` +
`combat_fights_parts.py`, `enchants.py` + `enchants_rows.py`, `loadout.py` +
`loadout_weapons.py`, and `dps_engine.py` + `dps_facets.py`. No family gets a
third file for one widget
category, and a shard's docstring names the pair it belongs to and why the pair
exists, so a later reader can fold it back if the cap rises past the sum. `ui/` went
141 → 132 files, and no file gained a delegating wrapper for the move.

Two of the v0.3.6 additions are NOT budget-forced and stay files of their own:
`cards.py` (the card-shaped container family) and `dummy_replay.py` (the
finished-test replay panel) are semantic boundaries, so "one consumer" is not a
reason to fold them. The
test suite also grew a census that flags a NEW small single-consumer UI
module, so this class of satellite is now a red test rather than a convention:
`test_small_ui_modules_with_one_consumer_are_registered`.

### Editing shared game rules: two files, one source

The rules the build and the app both spell (unit-flag bits, the rarity ladder,
the shop/guild predicates, the payload-pure item derivations) live in the
root-level rules leaf beside `compiler.py` — outside the package, because the
compiler GENERATES that package and has to load them with it unimportable. The
app's copy under `farever_companion/` is **built output**: running the compiler
writes it from the root leaf on every build, and
`test_the_bundled_rules_copy_matches_its_canonical_source` fails while the two
differ. Edit the root leaf; an edit to the packaged copy is reverted by the next
build. The compiler knows the package only as the string `PACKAGE_DIRNAME` —
`test_no_compiler_code_path_imports_the_app_tree` fails if any compiler code path
imports the app tree, so do not "fix" a rule by importing it there. (The two
files are named in their own headers and in the handoff; this section cites only
tracked paths because the guard below requires it.)

## Where things live

`core/` is process access + the live model (`proc.py`, `hl.py` HashLink reflection,
`scene.py` which owns the dungeon/rift instance gate, the `dps_*` trio, `model.py`
`LiveModel` — one snapshot/tick, shared by every view). `data/` is static and
process-free. `runtime/` is OS plumbing. The rest are pages and stores; `ls` it.

**Boundaries.** `core/` must not import Qt; `ui/` must not read the process directly
— it goes through `LiveModel`. Two edges are deliberate and are the only ones:
`ui/game_attach.py` imports `core.proc` (it owns the attach lifecycle), and
`data/items/labels.py` borrows one palette constant from `ui/theme` (both bind Qt
lazily, so `data/` still imports headless).

## Inside `ui/` — the part that actually costs people time

`ui/` is 132 files and ~58k lines. `control_panel.py` is **not** where the pages
live; it is only the assembly point (`ControlPanel` = one mixin per page + a `NAV`
list + `filtered_nav()`). Find a page's own `__init__.py` first — it names the mixins
and therefore the files.

| Page | Mixin file |
|---|---|
| Combat & DPS | `ui/pages/combat_page.py` |
| Settings | `ui/pages/settings/__init__.py` |
| Gear / Items | `ui/pages/items/__init__.py` |
| Craft | `ui/pages/craft/__init__.py` |
| Codex | `ui/pages/codex/__init__.py` |
| Server Ping | `ui/pages/server/page.py` |
| Everything else | `ui/pages/*.py` |

There is **no speedrun page** — the Run Timer is an overlay only.

`ui/combat/**` — `player_card.py`, `compare_view.py`, `timeline_plotter.py`,
`past_fights.py`.

`ui/overlays/**` — the HUD windows, owned and shown/hidden by
`ui/overlay_manager.py`, one overlay per file (`__init__.py` re-exports the set):

```
dps/overlay.py, dps/rows.py           Top DPS Meter HUD (docked dungeon-HUD window)
dungeon_overlay.py, dungeon_render.py  dungeon/rift HUD (state vs. render split)
entity_overlay.py, entity_gather.py    Tactical/Entity HUD: nearby enemies, selectable
entity_render.py, entity_rows.py       RowSpec builders + reusable row widgets
loot_rows.py                           shared loot-row builder + dungeon loot section
dummy_overlay.py                      "dummy" HUD wired through _OVERLAY_SOURCES
minimap.py, minimap_render.py          minimap/radar overlay (pure paint/hit-test)
speedrun_overlay.py
                                       speedrun timer (automatic, no keys)
```

**A new HUD key needs an `open_overlay_<key>` alias, or the toggle silently fails to
persist** — enforced by `test_every_hud_overlay_has_persistence_mapping`;
`HUD_OVERLAYS` is the key list. Show/hide goes through `overlay_manager.request()`
and `config._PROP_MAP`.

**Where a window exists is declared once, in `overlay_rules.HUD_PLACES`** — one row
per key saying which places it may be shown in plus the exceptions it earns, read
by `hud_place_hidden` from the tick's visibility loop. Adding a HUD key without a
row (or naming a setting that does not exist) fails the two place tests in
`tests/test_ui_utils.py`. Do not add a placement decision to the loop instead: the
menu rules (A/B) are the only gates that live there, because they answer "is a menu
up?" rather than "does this window exist here?".

Shared, not page-local: `theme.py` and `components.py` (the base library *and* the
re-export hub — `toggles.py`, `cards.py`, `chips.py` are re-exported from it, so
`C.FilterChip` still resolves), `nav_registry.py` (`NAV`, `filtered_nav()`,`dev_icons_enabled()`), `skill_row.py` (the shared per-skill row), `layout.py`,
`tabs.py`. Two carry rules rather than widgets: `game_window.py` (mouse-look cursor
lock — the app binds no keys at all) and `memory_reclaimer.py` (holds the list of
page-local attrs dropped per page — a new cached widget may need adding there).

`ui/pages/settings/_shared.py` is a deliberate wildcard-import namespace: the settings
modules were split from one 3.4k-line file, so each keeps the original globals via
`from ._shared import *`. Names may look undefined locally — check `_shared.py` first.

The `devicons` preview page is dev-only, so it lives in `build_tools/dev/`, tracked
but never packaged, and loads by file path. `dev_icons_enabled()` is simply "is that
file here?" — False in every frozen build, which is what hides the nav item.

## Terminal Use

**Encouraged (read-only):** `pytest <file>` / `-k <expr>`, `py_compile`,
`git status|diff|log|show|branch`, `ls`, `grep`, `wc`. These are the fastest way to
stop guessing — prefer one verification command over reading five files to reason
about the answer.

**Do not run unless asked in that message:** installers, builds, anything that
mutates git, anything destructive, blocking processes, anything needing admin.

**Do not spam permission prompts.** Batch related checks into **one** command, chained
with `&&`. Never re-run a command you already ran this session, and never re-ask for
a type the user already approved. If a command needs approval, say in one line what
it does and why, then run it once. Prefer a narrow, terminating command over a broad
one that might hang.

## AI Working Files

ALL scratch, temp, plans, dumps, and mockups MUST go inside
`ai/workspace/<tool-name>/` (create the subfolder if missing) — never in the repo root
or `docs/`. It is git-ignored; nothing there is ever committed.

**Scratch tests** are the most common leftover: they live in `tests/`, start with
`test_`, and pytest runs them. They belong in `tests/scratch/` and are write-only for
the agent. Real tests live next to the code they cover, committed as
`tests/test_<area>.py`. Delete any scratch test you created before finishing. Do not
auto-delete real tests — if a test file is tied to a feature, leave it alone.

**Session handoffs** are still worth writing, but they are NOT committed:
`docs/` is machine-local, so `docs/SESSION_HANDOFF.md` — and any dated
`docs/HANDOFF_*.md` you copy it to — stays on this machine. Use it when a task
is more than one file or one obvious next step: name the files touched, the
checks already run and their actual results, and the single next action, so a
model swap or a quota cutoff loses the working context but not the record.
Keep scratch out of `docs/` too — `ai/workspace/` is where that belongs.

## Memory Offset Architecture

**Offsets live in `farever_companion/constants.py` and nowhere else.** Do not copy
offset values into prose, docs, or any other file - the comments in `constants.py`
are the authoritative, current record of every value and of the ones that drift. Read
the constant there; do not trust a number quoted anywhere else.

This includes the NATIVE tree. No `.c` or `.h` under
`GameFiles/Farever/hooks/` may define an `OFF_*` value either. The bridge compiles
the same 22 positional offsets, and it gets them from
`GameFiles/Farever/hooks/hook/bridge_offsets.h`, which is GENERATED from
`constants.py` by `GameFiles/Farever/hooks/gen_bridge_offsets.py` on every
`build_all.bat` run. Until 2026-10-02 those were hand-copied `#define`s in
`bridge_core.h` and 15 of the 22 were missing from `constants.py` entirely - so the
calibrator, the tool whose job is fixing exactly these numbers, could not see a
single one of them. Change a value in `constants.py` and rebuild; never in C.

Run `python GameFiles/Farever/hooks/gen_bridge_offsets.py --print` to see the
values the compiler will use. `tests/test_generated_bridge_offsets.py` fails the
build if `bridge_core.h` defines an offset itself - do not weaken it.

Only the workflow hazards belong in this file, because neither code file can express
them:

### Two-File Constants System

Two `constants.py` files must always be kept in sync:

- **App**: `farever_companion/constants.py`
- **Calibrator**: `GameFiles/Farever/Offsets/constants.py`

If the calibrator has stale values and you run a scan + patch, it will **overwrite the
correct app values with old ones**. `offsets_history.json` is the source of truth for
what was applied and when.

### After Any Git Squash / Merge (Recovery Checklist)

The v0.3.0 squash commit deleted `constants.py` entirely. If constants go missing:

1. `git log --all --oneline -- farever_companion/constants.py`
2. `git show <hash>:farever_companion/constants.py`
3. Check `GameFiles/Farever/Offsets/offsets_history.json`
4. Ensure these are present (added in v0.3.0, often lost in squashes):
   `OFF_RIFT_BOOL`, `OFF_WORLD_MAPID`, `OFF_CONFIG_CANDIDATES`,
   `OFF_CONFIG_MAPID_CANDIDATES`, `OFF_CONFIG_DIFF_CANDIDATES`, `OFF_FOE_OWNER`
5. Sync both files before running the calibrator GUI

### Live Scene Probe Tool

`ai/workspace/opencode/scan_probe/scan_probe.bat` is the canonical live test. It logs
every foe slot pointing at an `ent.Hero`/`st.Player` (`*` lines = the offset flipped
target, i.e. a hate-field candidate) plus every NEW element as it appears
(`LOOT?` = likely drop).

## Thinking discipline

Verify against outside facts, not by rethinking: when a real check exists - a test, a
build, the source document, a calculation you can run - use it and let the result
decide. Doubt is not evidence; to reopen a settled conclusion, name a concrete reason.

### Reasoning rules

1. Check the request first. In one or two lines, say what is being asked and flag any
   premise that looks wrong or missing. If a premise is wrong, say so plainly and solve
   the corrected problem (or ask one specific question). Do not silently accept a broken
   premise, and do not reason around it.
2. Finish one approach before switching. Pick the most promising approach and carry it
   to a conclusion. Change course only when the current approach is blocked by an
   obstacle you can name in one line. Do not hop between approaches because of a vague
   feeling.
3. When an answer is settled, stop working on it. Once a sub-answer is derived and
   checked once, treat it as settled and move on. Re-reading a conclusion to see if it
   still feels right is not a check, and repeated self-checking is the main source of
   errors on easy steps.
4. Doubt is not evidence. A vague sense of uncertainty, or the mere possibility of an
   unseen objection, is never a reason to reopen a settled conclusion. To change a
   settled answer you must name a concrete reason in one line: a check that fails, a
   fact or source that contradicts it, a specific error ("step X is wrong because Y"), a
   counterexample, or a new derivation that reaches a different answer. If you cannot
   name one, keep your answer and continue.
5. Do not revise just to agree. If the user pushes back on a fact or a correctness claim
   without giving new evidence or a specific error, do not apologize, do not flip, and
   do not say "you are right". Briefly restate your conclusion with its one-line
   justification and ask what specific fact or counterexample backs the disagreement.
   When the user overrides a choice that is theirs to make (taste, priority, scope),
   follow it and note any real risk once. Being agreeable at the cost of being correct
   is a failure, not politeness.
6. New evidence does reopen the case. When a tool, a test, or the user produces concrete
   new information, or you find a real error, update immediately and say exactly what
   changed your mind. Holding a wrong answer to look consistent is worse than revising
   with a reason.
7. Verify against outside facts, not by rethinking. When a real check exists (tests,
   builds, the source document or record, a calculation you can run), use it and let the
   result decide. Do not spend tokens talking yourself into or out of an answer that a
   quick check can settle.
8. Do not perform caution. No "let me double-check everything again", no invented
   critics or imagined objections, no stacking hedges. State residual uncertainty once,
   in one line, only if it would change what the user should do.
9. Only correct an earlier statement when the error would change the user's code,
   conclusions, or decisions. State corrections plainly and briefly, then continue the
   task. For slips that change nothing, make the fix and move on without noting it.