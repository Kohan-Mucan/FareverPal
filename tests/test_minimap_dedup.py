"""Minimap duplicate-marker guard tests.

Static databases and the live scene scan the same world spots (a static orb +
its live spawn, a live checkpoint over a static respawn point), so `_add_poi`
skips a POI whose kind-family is already present within ~2 world units. Unit
markers (enemy / hero / companion) must never merge — several mobs can
legitimately share one position.
"""
from __future__ import annotations

from farever_companion.ui.overlays.minimap import _Canvas


def _canvas() -> _Canvas:
    cv = _Canvas.__new__(_Canvas)
    cv._px = cv._py = 0.0
    return cv


def test_same_kind_same_spot_dedupes():
    cv = _canvas()
    pois = []
    assert cv._add_poi(pois, 10.0, 20.0, 0.0, "orb", "orbA", "orbA", max_dist=0)
    # identical spot, same kind family -> skipped
    assert not cv._add_poi(pois, 10.5, 20.5, 0.0, "orb", "orbB", "orbB", max_dist=0)
    assert len(pois) == 1


def test_cross_kind_family_dedupes():
    """A live checkpoint over a static respawn point is one marker."""
    cv = _canvas()
    pois = []
    assert cv._add_poi(pois, 10.0, 20.0, 0.0, "respawn", "res", "res", max_dist=0)
    assert not cv._add_poi(pois, 10.0, 20.0, 0.0, "checkpoint", "cp", "cp", max_dist=0)
    assert len(pois) == 1


def test_far_apart_or_different_family_still_adds():
    cv = _canvas()
    pois = []
    assert cv._add_poi(pois, 10.0, 20.0, 0.0, "orb", "orbA", "orbA", max_dist=0)
    # same kind, far away -> kept
    assert cv._add_poi(pois, 50.0, 20.0, 0.0, "orb", "orbC", "orbC", max_dist=0)
    # different family at the same spot (chest under an orb) -> kept
    assert cv._add_poi(pois, 10.0, 20.0, 0.0, "chest", "chest1", "chest1", max_dist=0)
    assert len(pois) == 3


def test_unit_markers_never_merge():
    """Two enemies stacked on one spot are both real units — keep both."""
    cv = _canvas()
    pois = []
    assert cv._add_poi(pois, 10.0, 20.0, 0.0, "enemy", "e1", "e1", max_dist=0)
    assert cv._add_poi(pois, 10.0, 20.0, 0.0, "enemy", "e2", "e2", max_dist=0)
    assert cv._add_poi(pois, 10.0, 20.0, 0.0, "hero_warrior", "h1", "hero1", max_dist=0)
    assert len(pois) == 3


def test_max_dist_still_enforced():
    cv = _canvas()
    pois = []
    assert not cv._add_poi(pois, 100.0, 100.0, 0.0, "orb", "far", "far", max_dist=50.0)
    assert len(pois) == 0