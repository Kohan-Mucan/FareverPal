"""The HUD windows: one module (or package) per overlay, resolved at runtime.

This `__init__` imports nothing, and that is load-bearing twice over.
`OverlayManager._OVERLAY_SOURCES` maps each HUD key to its module and class, and
`_overlay_class` imports it by string the moment the window is first built, so
opening the app idle costs no overlay imports at all. And because there is no
static import edge anywhere below this line, PyInstaller's analysis cannot find
these modules either - the list in `FareverPal.spec` is the only thing that
carries them into a frozen build. Do not "tidy" re-exports in here: the hygiene
suite checks that list against `_OVERLAY_SOURCES` in both directions, and the
first version of that list was already stale by one module.

- dps/                  the Top DPS Meter (its own package: overlay, rows,
                        widgets, dummy_ui, dummy_replay)
- dummy_overlay.py      the Test Dummy HUD (the meter stripped to a dummy
                        parse; shown only while a dummy is in range, Rule F)
- entity_overlay.py     nearby enemies, selectable
- minimap.py            chests, gatherables, enemies (+ minimap_render paint)
- speedrun_overlay.py   the Run Timer (clock tiles + its Activity Log
                        edge diagnostics)
- dungeon_overlay.py    the dungeon/rift HUD (+ dungeon_render sections)

The rest are shared row/section builders, not windows: `entity_gather.py`,
`entity_render.py`, `entity_rows.py`, `loot_rows.py`. Only `overlay_manager`
imports this package, and only by the strings above; `ControlPanel` reaches the
overlay *cards* through `ui/pages/overlays.py`, which is a different thing.
"""
