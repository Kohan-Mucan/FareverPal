"""Settings DPS deep links (headless Qt, offscreen).

The Combat & DPS page gear button navigates with ``_select_nav("settings:dps")``
and legacy aliases such as ``settings:combat`` / ``settings:meter``. These must
land on the Settings > DPS sub-tab and re-sync the Combat DPS Meter card plus
the capture-engine controls from live settings, so an overlay-side change (view
chips, range cycle, capture-mode select, HP-diff fallback) shows up the moment
the user deep-links back into the tab -- even when the page is still cached.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

from farever_companion.config import Settings  # noqa: E402
from farever_companion.ui.control_panel import ControlPanel  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def panel(tmp_path_factory):
    """One real ControlPanel for the whole module (built once, like the
    page-eviction tests). Settings data is isolated to a temp dir."""
    os.environ["FAREVER_MODDATA_DIR"] = str(tmp_path_factory.mktemp("moddata"))
    cp = ControlPanel(Settings())
    yield cp
    cp.close()


def _assert_on_dps_tab(cp) -> None:
    """The Settings page is showing with the DPS sub-tab active."""
    assert cp._settings_tabs.currentText() == "DPS"
    assert cp._settings_stack.currentIndex() == 2   # Layers, HUD's, DPS, ...


def _external_writers(cp, view: str, dist: float, mode: str,
                      hp_est: bool | None = None) -> None:
    """Simulate another surface (overlay chips / range cycle / engine picker)
    writing settings while the DPS tab is not on screen."""
    cp.s.dps_view = view
    cp.s.dps_max_dist = dist
    cp.s.dps_mode = mode
    if hp_est is not None:
        cp.s.dps_hp_est = hp_est


@pytest.mark.parametrize("target", ["settings:dps", "settings:combat",
                                    "settings:meter", "settings:damage"])
def test_deep_link_lands_on_dps_tab_and_resyncs(panel, target):
    # Leave whatever tab the previous test ended on.
    panel._select_nav("overlays")

    # First open: the Settings page builds fresh from these external writes,
    # and the deep link still routes to the DPS sub-tab.
    _external_writers(panel, view="healing", dist=50.0, mode="memory",
                      hp_est=True)
    panel._select_nav(target)
    _assert_on_dps_tab(panel)
    assert panel._dps_view_seg.currentText().lower() == "healing"
    assert panel._dps_range_seg.currentText() == "50m"
    assert panel._dps_hp_est_toggle.isChecked() is True
    # The capture engine is picked by the option cards now (the old segmented
    # control is gone); the resync must surface the external mode write.
    assert panel.s.dps_mode == "memory"
    assert panel._dps_mode_cards["memory"] is not None

    # Leave, change settings behind the page's back, then deep-link again.
    # The page is still cached (no rebuild), so the resync is what brings the
    # widgets up to date.
    panel._select_nav("overlays")
    _external_writers(panel, view="both", dist=100.0, mode="injector",
                      hp_est=False)
    panel._select_nav(target)
    _assert_on_dps_tab(panel)
    assert panel._dps_view_seg.currentText().lower() == "both"
    assert panel._dps_range_seg.currentText() == "100m"
    assert panel._dps_hp_est_toggle.isChecked() is False
    assert panel.s.dps_mode == "injector"
    assert panel._dps_mode_cards["injector"] is not None
