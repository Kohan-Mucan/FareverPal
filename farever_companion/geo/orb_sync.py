"""Auto-detect collected secret orbs from the live scene.

World orbs never despawn client-side, and their `currentVisualState` flaps
with chunk activation - useless. The reliable signal (captured through a live
collection 2026-06-07): the glow-fx pointer. An uncollected orb carries
`currentFx` at any loaded distance (seen set at 260m); collecting it nulls
the fx instantly while the state stays 'Enabled'. So: collected = loaded
RedOrb_World element with no fx. Both directions are debounced over two
consecutive observations: marking needs fx-less twice (fx can spawn a tick
late on chunk load), and UN-marking needs glowing twice - a single-tick
bad fx read (stale offset, chunk flap) must never wipe a collected orb from
the done list. Pure logic; the caller feeds observations and applies the
returned deltas.
"""
from __future__ import annotations

LIVE_PREFIX = "RedOrb_World"


class FxSync:
    def __init__(self) -> None:
        self._pending: set[str] = set()      # fx-less once; confirm next tick
        self._glow_pending: set[str] = set() # glowing once; confirm next tick

    def update(self, live: list[tuple[str, bool]], done: set[str]
               ) -> tuple[list[str], list[str]]:
        """`live` = [(orb_id, fx_present)] for loaded RedOrb_World elements,
        `done` = currently-marked-collected ids. Returns (mark, unmark)."""
        mark: list[str] = []
        unmark: list[str] = []
        seen_dark: set[str] = set()
        seen_glow: set[str] = set()
        for oid, fx in live:
            if fx:
                seen_glow.add(oid)
                self._pending.discard(oid)
                if oid in done:
                    if oid in self._glow_pending:
                        unmark.append(oid)   # glowing twice = really uncollected
                    else:
                        self._glow_pending.add(oid)
            else:
                seen_dark.add(oid)
                self._glow_pending.discard(oid)
                if oid in done:
                    continue
                if oid in self._pending:
                    mark.append(oid)         # fx-less twice in a row
                else:
                    self._pending.add(oid)
        self._pending &= seen_dark           # drop pendings that unloaded
        self._glow_pending &= seen_glow      # drop glows that unloaded
        return mark, unmark
