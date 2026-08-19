"""Entity HUD soulstone waypoint row icon tests (headless Qt).

Regression: once soulstone tracking survives while attached (see
test_tracker.py), the Entity HUD renders a WAYPOINT row for the tracked
summon spot. That row builds its marker from the static-POI kind, and kind
'soulstone' used to fall through to the generic 'map-pin' marker — an asset
that doesn't exist — so `icons.marker()` returned None and
`QLabel.setPixmap(None)` crashed the overlay's _tick.

The HUD now maps soulstone to its real marker asset, and `icons.marker()`
never returns None for a missing asset (it degrades to an empty pixmap).
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

from farever_companion.data import icons  # noqa: E402
from farever_companion.ui.overlays import entity_render  # noqa: E402
from farever_companion.ui.overlays.entity_rows import (  # noqa: E402
    RowSpec, _EntityRow)


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _waypoint_row_spec(marker: str) -> RowSpec:
    """The exact RowSpec the Entity HUD builds for an unselected static
    waypoint (accent=None, outlined=True) — the shape that used to crash."""
    return RowSpec(
        None, None, None, "Mortalkombaal", "#e2e8f0",
        sub="Soulstone", value="1234m",
        bold=False, highlight=False, outlined=True,
        marker=marker, key=("static", "s123.0,456.0"),
        size_override=None)


def test_static_waypoint_kind_maps_to_soulstone_marker():
    """The HUD's kind -> marker-name mapping must resolve soulstone to its
    own gem marker asset (previously it fell through to the non-existent
    'map-pin', which is what made the icon come back None)."""
    assert entity_render._static_waypoint_marker("soulstone") == "soulstone"
    # the other static kinds keep their existing markers
    assert entity_render._static_waypoint_marker("dungeon") == "dungeon"
    assert entity_render._static_waypoint_marker("rift") == "rift"
    assert entity_render._static_waypoint_marker("obelisk") == "obelisk"
    assert entity_render._static_waypoint_marker("respawn") == "respawnpoint"


def test_soulstone_marker_asset_exists():
    """The 'soulstone' marker resolves to a real pixmap (the bundled
    assets/map_icons/soulstone.svg) — even without an accent."""
    pm = icons.marker("soulstone", 28)
    assert pm is not None and not pm.isNull()


def test_missing_marker_asset_never_returns_none():
    """A marker name with no asset (e.g. the old 'map-pin' fallback, or any
    unknown kind) degrades to an empty pixmap instead of None, so
    QLabel.setPixmap can never crash on it."""
    pm = icons.marker("map-pin", 28)
    assert pm is not None and not pm.isNull()
    pm2 = icons.marker("does_not_exist_xyz", 28)
    assert pm2 is not None and not pm2.isNull()


def test_entity_row_applies_soulstone_waypoint_without_crashing():
    """The HUD's static waypoint row renders for a tracked soulstone: the
    row applies the marker tile without raising, and the icon tile holds a
    real pixmap (previously setPixmap(None) raised a TypeError)."""
    row = _EntityRow(28)
    spec = _waypoint_row_spec("soulstone")
    row.apply(spec, 28)  # the exact call chain that crashed in _tick
    assert not row.tile.pixmap().isNull()


def test_entity_row_survives_unknown_marker_kind():
    """Even if a future kind still maps to a missing marker name, the row
    applies quietly (empty tile) instead of raising."""
    row = _EntityRow(28)
    row.apply(_waypoint_row_spec("map-pin"), 28)
    assert not row.tile.pixmap().isNull()
