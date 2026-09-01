"""Offscreen UI tests for the Combat DPS Meter settings card.

The Top DPS overlay's cycle button was removed in favor of a proper settings
card (Settings > DPS > Combat DPS Meter): tracking range, default view and
row limits persist through ``dps_max_dist`` / ``dps_view`` / ``dps_top_count``
/ ``heals_top_count``, and the open overlay adopts changes to range and view
live each tick while its own view chips still write back to settings.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6 import QtWidgets  # noqa: E402

from farever_companion.ui.pages.settings import SettingsPageMixin  # noqa: E402
from farever_companion.ui.overlays.dps_overlay import DpsOverlay  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _S:
    dps_max_dist = 400.0
    dps_view = "damage"
    dps_top_count = 8
    heals_top_count = 2
    dps_solo_only = True
    dps_mode = "proxy"
    dps_hook_enabled = False
    dps_hp_est = False

    def save(self):
        pass


class _Panel(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.s = _S()
        self._settings_steppers = []
        self._calls = []

    def _set(self, attr, value):
        setattr(self.s, attr, value)
        self._calls.append((attr, value))


class Panel(_Panel, SettingsPageMixin):
    pass


def _card():
    p = Panel()
    p._dps_card = p._build_dps_card()   # keep the widget tree alive (no parent)
    # Tracking range now lives in the Live Diagnostics card (moved out of the
    # meter card), so build that too for _dps_range_seg.
    p._dps_diag_card = p._build_dps_diagnostics_card()
    return p, dict(p._settings_steppers)


def test_dps_card_builds_from_settings_and_persists_changes():
    p, st = _card()
    assert p._dps_range_seg.currentText() == "400m"
    assert p._dps_view_seg.currentText().lower() == "damage"
    assert set(st) == {"dps_top_count", "heals_top_count"}
    assert st["dps_top_count"].value() == 8
    assert st["heals_top_count"].value() == 2

    p._set_dps_range("Zone")
    assert p.s.dps_max_dist == 0.0 and ("dps_max_dist", 0.0) in p._calls
    p._set_dps_range("100m")
    assert p.s.dps_max_dist == 100.0

    p._set_dps_view("Both")
    assert p.s.dps_view == "both"

    st["dps_top_count"].setValue(12)
    assert p.s.dps_top_count == 12
    st["heals_top_count"].setValue(5)
    assert p.s.heals_top_count == 5


def test_dps_card_resync_surfaces_external_changes():
    """Overlay-side changes (view chips write settings) appear on the card
    again when the DPS tab is re-shown, without a restart."""
    p, st = _card()
    p.s.dps_view = "healing"
    p.s.dps_max_dist = 50.0
    p.s.dps_top_count = 3
    p._resync_dps_settings_widgets()
    assert p._dps_view_seg.currentText().lower() == "healing"
    assert p._dps_range_seg.currentText() == "50m"
    assert st["dps_top_count"].value() == 3


def test_dps_card_solo_only_toggle_defaults_on_and_persists():
    """The solo-only view toggle ships ON and persists flips through _set."""
    p, _ = _card()
    assert p._dps_solo_only_toggle._toggle.isChecked() is True

    p._dps_solo_only_toggle._toggle.setChecked(False)
    assert p.s.dps_solo_only is False
    assert ("dps_solo_only", False) in p._calls

    # external change (e.g. another surface) resurfaces on re-show
    p.s.dps_solo_only = True
    p._resync_dps_settings_widgets()
    assert p._dps_solo_only_toggle._toggle.isChecked() is True


class _Model:
    is_dungeon = False
    is_rift = False
    damage = None
    player_addr = 0x1111

    def player_xyz(self):
        return (0.0, 0.0, 0.0)

    def units(self):
        return []

    def player_name(self, a):
        return None

    def _hero_player_ptr(self, a):
        return None

    def _player_name_at(self, p):
        return None


class _Settings:
    opacity = 1.0
    geometry = {}
    dps_max_dist = 400.0
    dps_view = "damage"
    dps_top_count = 8
    heals_top_count = 2
    dps_hp_est = False

    def save(self):
        pass


def test_overlay_adopts_settings_live_and_chips_write_back():
    ov = DpsOverlay(_Model(), _Settings())
    try:
        ov._tick()
        assert ov._view_mode == "damage" and ov.tracker.max_dist == 400.0

        ov.s.dps_view = "healing"
        ov.s.dps_max_dist = 100.0
        ov._tick()
        assert ov._view_mode == "healing"
        assert ov.tracker.max_dist == 100.0

        # the mode pill still writes the setting on user interaction
        ov.mode_pill._btns["BOTH"].click()
        assert ov.s.dps_view == "both" and ov._view_mode == "both"

        # the HP-diff fallback toggle syncs to the tracker live each tick
        ov.s.dps_hp_est = False
        ov._tick()
        assert ov.tracker.hp_est_allowed is False
        ov.s.dps_hp_est = True
        ov._tick()
        assert ov.tracker.hp_est_allowed is True
    finally:
        ov.close()


def test_overlay_header_is_mockup_pill():
    """The header is the mockup control now: a DMG / HEAL / BOTH segmented
    pill in the title bar; the METER / VIEW dropdowns and the range cycle
    button are gone."""
    ov = DpsOverlay(_Model(), _Settings())
    try:
        assert not hasattr(ov, "range_btn"), "cycle button removed in favor of settings"
        assert not hasattr(ov, "_btn_dmg"), "mode tabs replaced by the pill"
        assert not hasattr(ov, "_btn_seg_auto"), "segment buttons replaced by tracker"
        assert not hasattr(ov, "mode_combo") and not hasattr(ov, "seg_combo")
        assert set(ov.mode_pill._btns) == {"DMG", "HEAL", "BOTH"}

        # the pill drives the tracker view + settings
        ov.mode_pill._btns["HEAL"].click()
        assert ov._view_mode == "healing" and ov.s.dps_view == "healing"
    finally:
        ov.close()


def test_dps_engine_settings_tab_options():
    p = Panel()
    w = p._tab_dps()
    assert hasattr(p, "_dps_mode_cards")
    assert "proxy" in p._dps_mode_cards
    assert "injector" in p._dps_mode_cards
    assert "memory" in p._dps_mode_cards

    # Option 1: Proxy
    p._set_dps_mode_key("proxy")
    assert p.s.dps_mode == "proxy"
    assert p.s.dps_hook_enabled is False

    # Option 2: Injector
    p._set_dps_mode_key("injector")
    assert p.s.dps_mode == "injector"
    assert p.s.dps_hook_enabled is True

    # Option 3: Memory
    p._set_dps_mode_key("memory")
    assert p.s.dps_mode == "memory"
    assert p.s.dps_hook_enabled is False


def test_dps_engine_hp_est_toggle_persists_and_resyncs():
    """The HP-diff fallback toggle is off by default and persists writes;
    external settings changes surface on the widget via resync."""
    p = Panel()
    w = p._tab_dps()   # keep the page (and its children) alive
    assert hasattr(p, "_dps_hp_est_toggle")
    assert p._dps_hp_est_toggle.isChecked() is False

    p._dps_hp_est_toggle._toggle.setChecked(True)    # user flips it on
    assert p.s.dps_hp_est is True
    assert ("dps_hp_est", True) in p._calls

    # external change surfaces without firing another write
    p.s.dps_hp_est = False
    p._resync_dps_settings_widgets()
    assert p._dps_hp_est_toggle.isChecked() is False