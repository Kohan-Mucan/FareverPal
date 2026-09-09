"""Static world geometry: where things are, and which zone that is.

`chests`, `pois`, `gatherables` and `orbs` are position indexes in
global world XYZ (1:1 with the runtime player struct); `zones` resolves a live
position to a zone id, `nav` is the compass math the minimap shares, and
`orb_sync` is the pure policy for marking a world orb collected. Process-free
like `data`, and it imports only `data` and `constants` - never `core` or
`ui` - so the scene reader, the codex and the minimap share it cycle-free.
"""
