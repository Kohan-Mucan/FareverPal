"""The live layer: process access, and the model every view shares.

`proc` is the one read API over the Rust reader / pymem and `hl` the HashLink
reflection; `player` + `appsingleton` locate the local player, `scene` walks
the scene graph, `model` is the LiveModel, and the `dps_*` modules turn real
damage events into CombatSessions. No Qt anywhere in here: `ui` imports
`core`, never the reverse. AGENTS.md's map has the per-module detail.
"""
