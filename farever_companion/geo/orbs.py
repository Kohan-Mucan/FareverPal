"""Static secret-orb index: the world RedOrb_World placements.

Loads `notes/orb_positions.json` (284 orbs resolved from the world prefabs:
99 zone-baked per region toward the "Collector of <region>" achievements,
plus two inferred Z1 placements and 84 real Z3 placements). Coordinates are
global world XYZ, same frame as chests. Pure data, no attached process.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Orb:
    orb_id: str
    x: float
    y: float
    z: float
    region: str          # Z1 | Z2 | Z3
    zone: str | None

    def dist(self, x: float, y: float, z: float) -> float:
        return math.dist((self.x, self.y, self.z), (x, y, z))

    def dist2d(self, x: float, y: float) -> float:
        return math.hypot(self.x - x, self.y - y)


REGION_NAMES = {
    "Z1": "Skover Island",
    "Z2": "Valley of Eternal Autumn",
    "Z3": "Ramburg",
}


@lru_cache(maxsize=1)
def load_orbs() -> list[Orb]:
    from ..data import cdb
    raw = cdb.lines("orb_positions")
    # Handle the case where the data is wrapped in a dict {'orbs': [...]}
    if isinstance(raw, dict):
        raw = raw.get("orbs", [])
        
    out = []
    for o in raw:
        if not isinstance(o, dict) or not o.get("id"):
            continue
        # Normalize coordinates to float — the source JSON may encode whole
        # numbers as int (e.g. "z": 348) and every consumer does float math.
        try:
            x, y, z = (float(o["x"]), float(o["y"]), float(o["z"]))
        except (KeyError, TypeError, ValueError):
            continue
        out.append(Orb(
            orb_id=o["id"], x=x, y=y, z=z,
            region=o.get("region") or "?", zone=o.get("zone") or None,
        ))
    return out


@lru_cache(maxsize=1)
def by_id() -> dict[str, Orb]:
    return {o.orb_id: o for o in load_orbs()}


def orb_label(orb_id: str) -> str:
    """Readable name for an orb id: 'RedOrb_World_169' -> 'Secret Orb 169'."""
    n = orb_id.rsplit("_", 1)[-1]
    return f"Secret Orb {n}" if n.isdigit() else f"Secret Orb · {orb_id}"


def orb_region_name(orb: Orb) -> str:
    return REGION_NAMES.get(orb.region, orb.region)


def region_progress(done_ids) -> dict[str, tuple[int, int]]:
    """{region -> (marked done, total)} for the regions that have orbs."""
    done = set(done_ids)
    out: dict[str, list[int]] = {}
    for o in load_orbs():
        tot = out.setdefault(o.region, [0, 0])
        tot[1] += 1
        if o.orb_id in done:
            tot[0] += 1
    return {r: (d, t) for r, (d, t) in sorted(out.items())}
