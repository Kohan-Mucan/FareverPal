"""Static, process-free game data: the CDB sheets, the shims, the lookups.

`cdb` reads the shipped sheets, `raw_*` are the compressed blobs the compiler
bakes (regenerate them, never hand-edit), and names / icons / units / skills /
codex are the lookups built on them. Importable headless - `icons` binds Qt
lazily, and the one edge back up the stack is `items/labels` borrowing a
palette constant from `ui/theme` - so `core`, `geo` and the UI may all import
it, but nothing here reads the process.
"""
