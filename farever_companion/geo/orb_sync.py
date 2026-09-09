"""Auto-detect collected secret orbs from the live scene.

World orbs never despawn client-side, and their `currentVisualState` flaps
with chunk activation - useless. The reliable signal (captured through a live
collection 2026-06-07): the glow-fx pointer. An uncollected orb carries
`currentFx` at any loaded distance (seen set at 260m); collecting it nulls
the fx instantly while the state stays 'Enabled'. So: collected = loaded
RedOrb_World element with no fx. Marking is debounced over two consecutive
observations because fx can spawn a tick late on chunk load. Persisted marks
are never automatically removed: a glowing/late/unreadable observation is not
strong enough evidence to undo a user's collection. Only an explicit UI
un-mark action may remove an orb. Pure logic; the caller feeds observations
and applies the returned additions.
"""
from __future__ import annotations

LIVE_PREFIX = "RedOrb_World"


class FxSync:
    def __init__(self) -> None:
        self._pending: set[str] = set()      # fx-less once; confirm next tick
        self._glow_pending: set[str] = set() # glowing once; confirm next tick

    def update(self, live: list[tuple[str, bool]], done: set[str]
               ) -> tuple[list[str], list[str]]:
        """Return newly collected orb IDs; never return automatic removals.

        ``live`` is ``[(orb_id, fx_present)]`` for loaded RedOrb_World
        elements and ``done`` is the persisted collected set. A dark/fx-less
        observation is confirmed twice before it is added. A glowing
        observation is deliberately not allowed to remove a persisted mark:
        chunk activation and stale memory reads can make a collected orb glow
        temporarily. The second return value remains an empty list for API
        compatibility with older callers.
        """
        mark: list[str] = []
        seen_dark: set[str] = set()
        for oid, fx in live:
            if fx:
                self._pending.discard(oid)
                self._glow_pending.discard(oid)
                continue
            seen_dark.add(oid)
            self._glow_pending.discard(oid)
            if oid in done:
                continue
            if oid in self._pending:
                mark.append(oid)
            else:
                self._pending.add(oid)
        self._pending &= seen_dark
        self._glow_pending.clear()
        return mark, []
