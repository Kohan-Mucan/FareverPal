"""test_overlays - consolidated subsystem tests.

Merged without changing any test, from:
  * tests/test_dungeon_orbs.py
  * tests/test_entity_sections.py
  * tests/test_difficulty_badge.py
  * tests/test_rift_ding.py
  * tests/test_skills_section.py

Imports are deduped and the shared `_qapp` fixture kept once;
where two source files bound one name differently, the later
was renamed `<name>__<file>` and only its own file's references
follow. See AGENTS.md: the suite is the spec.
"""
from __future__ import annotations


# --- shared imports ---
import pytest
from farever_companion.ui.overlays.dungeon_overlay import DungeonOverlay
from PySide6 import QtWidgets
from farever_companion.ui.overlays.entity_overlay import (
    EntityOverlay, _ENTITY_SECTION_TOGGLES, _ENTITY_TOGGLES_BY_KEY,
)
from farever_companion.ui.overlays.entity_gather import EntityGatherMixin
from farever_companion.ui.overlays.entity_render import EntityRenderMixin
import os
from farever_companion.ui import dps_source_text as dst  # noqa: E402
from farever_companion.ui.overlays.dps import DpsOverlay  # noqa: E402
from farever_companion.ui.overlays.speedrun_overlay import SpeedrunOverlay  # noqa: E402
from farever_companion.ui.pages import combat_page  # noqa: E402
from tests.dps_fakes import _FakeModel as _Model__difficulty_badge  # noqa: E402
from tests.qt_helpers import destroy  # noqa: E402
import time
from farever_companion.ui import rift_box as rb_mod
from farever_companion.ui.rift_box import RiftSidebarMixin
from farever_companion import paths  # noqa: E402
from farever_companion.data import items as idata  # noqa: E402
from farever_companion.ui.pages.items.skills_section import (  # noqa: E402
    SkillBar, _ChainBlock)
from farever_companion.data import skills

# ========================================================================
# from tests/test_dungeon_orbs.py
# ========================================================================

"""Dungeon HUD SECRET ORBS gather: element state + trusted fx + hide.

2026-09-19 ("dungeon Hide Collected still does nothing"): the gather only
ever looked at the glow fx, and a dark orb with no glow history stays
"undecided" (the fresh-load/LOD guard). So entering a dungeon whose orbs were
collected long ago marked nothing, every orb drew its row, and the toggle had
nothing to hide. The element's OWN state is the game's verified done flag
(docs/CHEST_ORB_LOGIC.md: orb 'Enabled' = up for collection, 'Disabled' =
collected), needs no history, and now marks on first sight; the fx rules
remain for orbs whose state can't be read.

2026-09-18: marked collected orbs kept re-appearing red (Hide Collected
looked dead). Root cause: the gather block read the glow-fx pointer at the
hardcoded OFF_ELEM_FX slot and trusted whatever it found - after a patch
drifted the element layouts, a garbage in-range value read as "glowing" for
EVERY orb, and the `done_set.discard(eid)` un-done ran one tick at a time
until the persisted mark was gone and the red row came back.

The block now consumes model.orb_fx_present (reflected currentFx offset,
trusted-or-None) and an untrusted read is "no opinion": it can never un-done
a mark, so a hidden orb STAYS hidden while Hide Collected is on. The only
honest un-done is two consecutive trusted glows (debounced like the
world-orb sync in geo/orb_sync.py).
"""
pytest.importorskip("PySide6")
class _Element:
    """Just what the gather loop touches on a scene Element."""

    def __init__(self, eid, x=10.0, y=0.0, state=None):
        self.elem_id = eid
        self.x = x
        self.y = y
        self.addr = 0x2000 + int(x)
        # game state flag: 'Enabled' = up for collection, 'Disabled' =
        # collected, None = unreadable (keeps the fx rules)
        self.state = state

    def dist2d(self, x, y):
        return ((self.x - x) ** 2 + (self.y - y) ** 2) ** 0.5
class _Settings:
    """In-memory stand-in for the dungeon-orb done list (no disk)."""

    dungeon_hide_collected = True

    def __init__(self, done=()):
        self._done = list(done)

    def get_dungeon_orb_done(self, profile=None):
        return list(self._done)

    def toggle_dungeon_orb_done(self, eid, profile=None):
        if eid in self._done:
            self._done.remove(eid)
            return False
        self._done.append(eid)
        return True
class _Model:
    """Fake LiveModel: live orbs + a per-id fx answer (True/False/None)."""

    def __init__(self, orbs, fx, rift=False):
        self._orbs = list(orbs)
        self._fx = fx
        self._rift = rift

    def is_in_dungeon(self):
        return not self._rift

    def is_in_rift(self):
        return self._rift

    def live_orbs(self, player_zone=None, max_dist=0.0, use_2d=False):
        return list(self._orbs)

    def orb_fx_present(self, e):
        return self._fx.get(e.elem_id)
def _host(model, settings) -> DungeonOverlay:
    h = DungeonOverlay.__new__(DungeonOverlay)
    h.model = model
    h.s = settings
    return h
def test_persisted_done_orb_stays_hidden_when_fx_read_is_untrusted():
    """THE regression: layout drift -> fx untrusted -> the mark survives and
    the orb stays hidden. (Pre-fix, an untrusted read was coerced to
    'glowing', un-done the mark, and the red row came back.)"""
    orbs = [_Element("R1_POI_Dungeon_CrimsonBarraks_RedOrb1")]
    model = _Model(orbs, {"R1_POI_Dungeon_CrimsonBarraks_RedOrb1": None})
    s = _Settings(["R1_POI_Dungeon_CrimsonBarraks_RedOrb1"])
    h = _host(model, s)
    for _ in range(3):                      # many ticks, not just one
        h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert h._dg_orbs == []                 # hidden keeps standing
    assert s.get_dungeon_orb_done() == ["R1_POI_Dungeon_CrimsonBarraks_RedOrb1"]
def test_single_glow_tick_cannot_undo_persisted_done():
    """One trusted glow tick only PENDS the un-done (FxSync debounce rule)."""
    orbs = [_Element("Orb_A")]
    model = _Model(orbs, {"Orb_A": True})
    s = _Settings(["Orb_A"])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert s.get_dungeon_orb_done() == ["Orb_A"]
    assert h._dg_orbs == []                 # still marked -> still hidden
def test_glowing_live_state_never_undoes_persisted_done():
    """Only an explicit user action may remove a persisted dungeon-orb mark."""
    orbs = [_Element("Orb_A", state="Enabled")]
    model = _Model(orbs, {"Orb_A": True})
    s = _Settings(["Orb_A"])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert s.get_dungeon_orb_done() == ["Orb_A"]
    assert h._dg_orbs == []
def test_never_glowing_dark_orb_is_not_automarked():
    """A dark read with no prior glow (fresh load, LOD) stays undecided."""
    orbs = [_Element("Orb_B")]
    model = _Model(orbs, {"Orb_B": False})
    s = _Settings([])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert h._dg_orbs == orbs
    assert s.get_dungeon_orb_done() == []
def test_glow_then_dark_marks_done_and_hides():
    orbs = [_Element("Orb_C")]
    fx = {"Orb_C": True}
    model = _Model(orbs, fx)
    s = _Settings([])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    fx["Orb_C"] = False
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert s.get_dungeon_orb_done() == ["Orb_C"]
    assert h._dg_orbs == []                 # Hide Collected hides it
def test_untrusted_read_never_marks_done():
    """An orb seen glowing then going UNTRUSTED (not dark) is not collected."""
    orbs = [_Element("Orb_D")]
    fx = {"Orb_D": True}
    model = _Model(orbs, fx)
    s = _Settings([])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    fx["Orb_D"] = None
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert s.get_dungeon_orb_done() == []
    assert h._dg_orbs == orbs               # undecided -> keeps showing
def test_hide_collected_off_keeps_done_rows_visible():
    orbs = [_Element("Orb_E")]
    model = _Model(orbs, {"Orb_E": None})
    s = _Settings(["Orb_E"])
    s.dungeon_hide_collected = False
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert h._dg_orbs == orbs
def test_rift_skips_the_orb_scan():
    orbs = [_Element("Orb_F")]
    model = _Model(orbs, {"Orb_F": True}, rift=True)
    s = _Settings([])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert h._dg_orbs == []
def test_vanish_near_last_pos_marks_done():
    """Orb seen glowing then gone from the scan near its last position."""
    orbs = [_Element("Orb_G")]
    model = _Model(orbs, {"Orb_G": True})
    s = _Settings([])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)   # glowed at (10, 0)
    model._orbs = []
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert "Orb_G" in s.get_dungeon_orb_done()
def test_fresh_session_does_not_unmark_manual_done():
    """App restart (seen empty) with untrusted fx: a manually right-click
    marked orb is never wiped by any number of gather ticks."""
    orbs = [_Element("Orb_H")]
    model = _Model(orbs, {"Orb_H": None})
    s = _Settings(["Orb_H"])
    h = _host(model, s)
    for _ in range(4):
        h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert s.get_dungeon_orb_done() == ["Orb_H"]
    assert h._dg_orbs == []
def test_disabled_state_marks_on_first_sight_and_hides():
    """THE 2026-09-19 bug: orbs already collected when you walk in were never
    marked (dark, never seen glowing), so every one drew a red row and Hide
    Collected looked broken. The game's Disabled flag marks them at once."""
    orbs = [_Element("R1_POI_ManfishRuines_RedOrb_1", state="Disabled")]
    model = _Model(orbs, {"R1_POI_ManfishRuines_RedOrb_1": None})
    s = _Settings([])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert s.get_dungeon_orb_done() == ["R1_POI_ManfishRuines_RedOrb_1"]
    assert h._dg_orbs == []                 # hidden on the first tick
def test_state_decides_even_when_the_fx_read_is_untrusted():
    """State and fx are independent reads: an unreadable fx pointer must not
    veto a trustworthy Disabled state."""
    orbs = [_Element("Orb_S1", state="Disabled"), _Element("Orb_S2", state="disabled")]
    model = _Model(orbs, {})                # no fx answers at all -> None
    s = _Settings([])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert sorted(s.get_dungeon_orb_done()) == ["Orb_S1", "Orb_S2"]
    assert h._dg_orbs == []
def test_enabled_state_never_erases_persisted_done():
    """A trusted live state still cannot undo a user's collection mark."""
    orbs = [_Element("Orb_E", state="Enabled")]
    model = _Model(orbs, {"Orb_E": False})
    s = _Settings(["Orb_E"])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert s.get_dungeon_orb_done() == ["Orb_E"]
    assert h._dg_orbs == []
def test_disabled_state_beats_a_glowing_fx():
    """Conflict (state says collected, fx says glowing): the game's own state
    is authoritative, so the orb is marked rather than shown."""
    orbs = [_Element("Orb_X", state="Disabled")]
    model = _Model(orbs, {"Orb_X": True})
    s = _Settings([])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert s.get_dungeon_orb_done() == ["Orb_X"]
    assert h._dg_orbs == []
def test_unreadable_state_keeps_the_dark_read_undecided():
    """No state + dark + never glowing: still undecided (the LOD guard), so
    the fx-only behaviour is unchanged where state is unavailable."""
    orbs = [_Element("Orb_Z", state=None)]
    model = _Model(orbs, {"Orb_Z": False})
    s = _Settings([])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert s.get_dungeon_orb_done() == []
    assert h._dg_orbs == orbs
def test_unknown_state_string_falls_back_to_fx():
    """An unrecognised state value (a new game build) must not be guessed at:
    glow-then-dark keeps working through the fx rule."""
    orbs = [_Element("Orb_U", state="Weird")]
    fx = {"Orb_U": True}
    model = _Model(orbs, fx)
    s = _Settings([])
    h = _host(model, s)
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert s.get_dungeon_orb_done() == []
    fx["Orb_U"] = False
    h._refresh_dg_orbs((0.0, 0.0, 0.0), None)
    assert s.get_dungeon_orb_done() == ["Orb_U"]

# ========================================================================
# from tests/test_entity_sections.py
# ========================================================================

"""Entity HUD section toggles — Rift Timer + Loot (2026-09-19).

The Entity HUD rendered both sections but owned no switch for either: the rift
card's only control was the global `show_rift_timer` mode (Off / Active /
Always) in the Rift tab, and loot's was a World Layers matrix row. Both now
have a titlebar button on the HUD itself, plus a chip on the Settings Entity
HUD card, matching the Dungeon HUD's two-surface pattern.

The rift switch is deliberately a SECTION switch (`entity_show_rift`, the
counterpart of `dungeon_show_rift`) rather than a writer of the mode string,
so flipping it can never silently rewrite an "Active" mode to "Always".

These tests also include a REAL-constructor smoke: the Dungeon HUD shipped a
constructor crash on 2026-09-19 because every test built the overlay with
__new__ and never ran __init__.
"""
pytest.importorskip("PySide6")
class _Header:
    def __init__(self):
        self.text = ""
        self.tag = ""

    def set_text(self, t):
        self.text = t

    def set_tag(self, t):
        self.tag = t

    def hide(self):
        pass
class _Box:
    def __init__(self):
        self.header = _Header()
        self.specs = []
        self.visible = False

    def fill(self, specs, _isz):
        self.specs = list(specs)

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def setVisible(self, on):          # noqa: N802 - Qt spelling
        self.visible = bool(on)
class _Model__entity_sections:
    """Just enough for the empty-list render path + the rift card."""

    def player_xyz(self):
        return (0.0, 0.0, 0.0)

    def player_profile(self):
        return "test"

    def is_in_dungeon(self):
        return False

    def is_in_rift(self):
        return False
class _Drop:
    item_id = "Spark_Shard"
    count = 1
    rarity = "Epic"        # above the default Rare+ floor, so a row renders
    x, y, z = 1.0, 2.0, 0.0

    def dist2d(self, x, y):
        return 5.0
class _Rift:
    state = "SCHEDULED"
    formatted_time = "12:00"
    status_label = ""
    in_range = True
    at_any_rift = False
    subzone = "Manfish Ruines"
    rift_name = "Rift Manfish Ruines LIVE"
    poi_x = None
    poi_y = None
class _Host(EntityRenderMixin):
    """The real renderer over fake boxes and a real Settings."""

    # the mode parser lives on the overlay shell, not the render mixin — reuse
    # the real one so the test can't drift from it
    _get_rift_mode = EntityOverlay._get_rift_mode

    def __init__(self, s):
        self.s = s
        self._scale = 1.0
        self.model = _Model__entity_sections()
        self._sel = None
        self._owned = None
        self._enemies = []
        self._spark_mobs = []
        self._group_members = []
        self._comps = []
        self._gatherables = []
        self._orbs = []
        self._chests = []
        self._loots = []
        self._foods = []
        self._static_pois = []
        for name in ("rift_box", "enemy_box", "spark_box", "comp_box",
                     "group_box", "gather_box", "orb_box", "chest_box",
                     "loot_box", "food_box", "static_box"):
            setattr(self, name, _Box())

    def _is_tracked(self, kind, key):
        return False

    def _track(self, kind, key, addr=None):
        pass

    def _select(self, *a):
        pass

    def _hide_unit(self, uid):
        pass

    def _hide_companion(self, uid):
        pass

    def render(self):
        self._render_sections("test", [], 16, True, False)
@pytest.fixture()
def settings(tmp_path, monkeypatch):
    """A REAL Settings on a throwaway config dir (never the user's data)."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    from farever_companion.config import Settings
    return Settings()
def test_toggle_table_maps_to_real_settings_attrs(settings):
    assert [k for k, *_ in _ENTITY_SECTION_TOGGLES] == ["rift", "loot"]
    attrs = [spec[0] for spec in _ENTITY_TOGGLES_BY_KEY.values()]
    assert attrs == ["entity_show_rift", "show_loot"]
    assert settings.entity_show_rift is True      # sections default visible
    assert settings.show_loot is True
def test_closed_live_chest_does_not_erase_persisted_collection(settings):
    """A transient closed read during zoning must not re-show a collected POI."""
    class E:
        elem_id = "Z1_World_Greenlands_WorldChest_10"
        state = "Closed"
        x = y = z = 0.0

    class M:
        def live_chests(self, **kwargs):
            return [E()]
        def world_orb_fx(self):
            return []
        def obelisks(self, **kwargs):
            return []
        chests = []

    h = EntityGatherMixin.__new__(EntityGatherMixin)
    h.model = M()
    h.s = settings
    h._tracker = None
    done = ["Z1_World_Greenlands_WorldChest_10"]
    assert h._sync_done_state((0.0, 0.0, 0.0), "Mage", done, 0.0) == done
def test_entity_overlay_constructs_with_section_buttons(settings):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ov = EntityOverlay(model=None, settings=settings)
    try:
        assert sorted(ov._section_btns) == ["loot", "rift"]
        assert ov._section_btns["rift"].toolTip() == "Hide rift timer"
        ov.set_show_section("rift", False)
        assert settings.entity_show_rift is False
        assert ov._section_btns["rift"].toolTip() == "Show rift timer"
        ov.set_show_section("rift", True)
        assert settings.entity_show_rift is True
    finally:
        ov._timer.stop()
        ov.close()
def test_entity_hud_reports_the_size_it_takes_in_game(settings):
    """The live auto-height rule and the 1:1 preview's pin must be ONE rule.

    `_ingame_height` (body + titlebar chrome, capped to the screen) is what
    `_do_auto_height` resizes to and what `ingame_size` reports, so the dev
    page can show this HUD at the size it really takes instead of letting a
    body layout stretch it. The width is the scaled declared width the HUD
    enforces from settings (`entity_width`).
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    ov = EntityOverlay(model=None, settings=settings)
    try:
        ov.show()
        app.processEvents()
        assert ov.ingame_size().width() == round(ov._base_w * ov._scale)
        assert ov.ingame_size().width() >= ov.minimumWidth()
        assert ov.ingame_size().height() == ov._ingame_height()
        # the auto-height lands on exactly the reported height
        ov._do_auto_height()
        assert ov.height() == ov._ingame_height()
        assert ov.ingame_size().height() == ov.height()
    finally:
        ov._timer.stop()
        ov.close()
def _overlay_host(s):
    """__new__-built EntityOverlay with just the section-button surface."""
    ov = EntityOverlay.__new__(EntityOverlay)
    ov.s = s
    ov._section_btns = {}
    ov._tick = lambda: None
    return ov
def test_loot_toggle_writes_through_the_layer_prop_map(settings):
    ov = _overlay_host(settings)
    ov.set_show_section("loot", False)
    assert settings.show_loot is False
    assert "loot" not in settings.entity_layers     # the real store
    ov.set_show_section("loot", True)
    assert settings.show_loot is True
    assert "loot" in settings.entity_layers
    # unknown key is a no-op, not a crash
    ov.set_show_section("nope", False)
def test_rift_toggle_leaves_the_global_mode_untouched(settings):
    """Toggling the section must not rewrite the Off/Active/Always mode."""
    settings.show_rift_timer = "Active"
    ov = _overlay_host(settings)
    ov.set_show_section("rift", False)
    assert settings.entity_show_rift is False
    assert settings.show_rift_timer == "Active"
    ov.set_show_section("rift", True)
    assert settings.entity_show_rift is True
    assert settings.show_rift_timer == "Active"
def test_rift_card_needs_both_gates(settings, monkeypatch):
    """Section off hides the card with NO rift read at all; on, the card
    renders its row (state read through game_state)."""
    from farever_companion.ui.overlays import entity_render as er

    def _boom(_m):
        raise AssertionError("rift state must not be read for a hidden card")

    monkeypatch.setattr(er.game_state, "rift", _boom)
    host = _Host(settings)
    settings.entity_show_rift = False
    host.render()
    assert host.rift_box.visible is False

    monkeypatch.setattr(er.game_state, "rift", lambda _m: _Rift())
    settings.entity_show_rift = True
    host.render()
    assert host.rift_box.visible is True
    assert len(host.rift_box.specs) == 1
def test_rift_card_still_hides_on_mode_off(settings, monkeypatch):
    from farever_companion.ui.overlays import entity_render as er
    monkeypatch.setattr(er.game_state, "rift", lambda _m: _Rift())
    host = _Host(settings)
    settings.show_rift_timer = "Off"
    host.render()
    assert host.rift_box.visible is False
def test_loot_section_hides_once_the_toggle_is_off(settings):
    """Render side of the loot switch: rows show while the gather hands them
    over (gated on show_loot), and the section hides when it stops."""
    host = _Host(settings)
    drops = [_Drop()]
    host._loots = list(drops)
    host.render()
    assert host.loot_box.visible is True

    settings.show_loot = False                     # what the button writes
    host._loots = [] if not settings.show_loot else list(drops)   # gather gate
    host.render()
    assert host.loot_box.visible is False

# ========================================================================
# from tests/test_difficulty_badge.py
# ========================================================================

"""The instance difficulty badge, and the surfaces that must agree on it.

The Top DPS HUD, the DPS Analysis page and the Run Timer all answer one
question - "which difficulty is this instance?" - so they share one resolution
(`ui/dps_source_text.resolved_mode` / `instance_mode`).

The failure this pins (LIVE 2026-09-30, dungeon visit): the DPS surfaces read
`model.dungeon_mode()` alone - the GameLayer-config read - and showed NO mode
at all while the Run Timer named one, because on that build the config had
resolved to a UI object (`scene._looks_like_config_id`). A dead read must not
leave a surface blank when the app has a stated fallback for exactly that case;
and the fallback must never be presented as a read.
"""
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the module (widgets need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
class _Settings__difficulty_badge:
    opacity = 1.0
    geometry = {}
    dps_view = "damage"
    dps_top_count = 8
    heals_top_count = 2

    def save(self):
        pass
class _Model_(_Model__difficulty_badge):
    """The DPS fake, plus the two difficulty questions every surface asks."""

    is_dungeon = True
    is_rift = False

    def __init__(self, mode=None, *, inside=True, raises=False, **kw):
        super().__init__(**kw)
        self._mode = mode
        self._raises = raises
        self.is_dungeon = inside

    def detected_mode(self):
        if self._raises:
            raise RuntimeError("scene read died")
        return self._mode

    def dungeon_mode(self):
        return self._mode
@pytest.mark.parametrize("mode", ["normal", "hard", "heroic"])
def test_resolved_mode_names_the_game_as_the_source(mode):
    assert dst.resolved_mode(_Model_(mode)) == (mode, "auto")
def test_resolved_mode_normalises_case():
    """A case difference is not a second difficulty (the old speedrun compare
    was exact, so a capitalised read fell through to the fallback)."""
    assert dst.resolved_mode(_Model_("HARD")) == ("hard", "auto")
    assert dst.resolved_mode(_Model_("Heroic")) == ("heroic", "auto")
@pytest.mark.parametrize("junk", [" Hard ", "", "mythic", None])
def test_resolved_mode_ignores_anything_that_is_not_a_difficulty(junk):
    got_mode, src = dst.resolved_mode(_Model_(junk))
    assert src == "manual"          # junk is not a read
    assert got_mode == dst.MANUAL_FALLBACK_MODE
def test_resolved_mode_falls_back_to_hard_when_nothing_can_be_read():
    """No read, no model, or a raising read - all three still name a mode."""
    assert dst.resolved_mode(_Model_(None)) == ("hard", "manual")
    assert dst.resolved_mode(None) == ("hard", "manual")
    assert dst.resolved_mode(_Model_(raises=True)) == ("hard", "manual")
def test_instance_mode_only_guesses_inside_an_instance():
    """The fallback is for an instance; walking in town must not ghost it."""
    assert dst.instance_mode(_Model_(None, inside=True)) == ("hard", "manual")
    assert dst.instance_mode(_Model_(None, inside=False)) == (None, "")
    # Outside, a real read still shows (it is not a guess).
    assert dst.instance_mode(_Model_("hard", inside=False)) == ("hard", "auto")
def test_mode_tooltip_owns_the_fallback():
    assert "live game state" in dst.mode_tooltip("normal", "auto")
    assert "manual fallback" in dst.mode_tooltip("hard", "manual")
    assert dst.mode_tooltip(None, "") == ""
def test_top_dps_title_shows_the_fallback_mode_inside_a_dungeon():
    """The reported bug, on the surface it was reported on."""
    ov = DpsOverlay(_Model_(None, inside=True), _Settings__difficulty_badge())
    ov.show()
    QtWidgets.QApplication.processEvents()
    try:
        ov._tick()
        QtWidgets.QApplication.processEvents()
        title = ov.titlebar.title.text()
        assert "TOP DPS" in title
        assert "· HARD" in title
        assert "manual fallback" in ov.titlebar.title.toolTip()
    finally:
        destroy(ov)
def test_top_dps_title_stays_bare_out_in_the_world():
    ov = DpsOverlay(_Model_(None, inside=False), _Settings__difficulty_badge())
    ov.show()
    QtWidgets.QApplication.processEvents()
    try:
        ov._tick()
        QtWidgets.QApplication.processEvents()
        assert "TOP DPS" in ov.titlebar.title.text()
        assert "·" not in ov.titlebar.title.text()
    finally:
        destroy(ov)
def test_dps_analysis_header_badges_the_same_mode():
    host = combat_page.CombatPageMixin.__new__(combat_page.CombatPageMixin)
    host.cp_mode_badge = QtWidgets.QLabel("")
    try:
        host.model = _Model_(None, inside=True)
        host._refresh_combat_mode_badge()
        assert "· HARD" in host.cp_mode_badge.text()
        assert not host.cp_mode_badge.isHidden()
        assert "manual fallback" in host.cp_mode_badge.toolTip()

        host.model = _Model_(None, inside=False)
        host._refresh_combat_mode_badge()
        assert host.cp_mode_badge.isHidden()
    finally:
        # Parentless widget: destroy it, not just hide it (a live top-level
        # from a finished test drifts the geometry/overlay tests after it).
        destroy(host.cp_mode_badge)
def test_an_archived_fight_badges_its_own_difficulty():
    """Loading a past fight shows the difficulty it was RECORDED at, not the
    one you are standing in now — the log's name carries it, so it survives the
    run (this is what "auto load the difficulty" means)."""
    from farever_companion.core.dps_tracker import CombatSession

    host = combat_page.CombatPageMixin.__new__(combat_page.CombatPageMixin)
    host.cp_mode_badge = QtWidgets.QLabel("")
    try:
        # out in the open world, with nothing archived: no badge at all
        host.model = _Model_(None, inside=False)
        host._browse_sessions = None
        host._selected_history_idx = 0
        host._refresh_combat_mode_badge()
        assert host.cp_mode_badge.isHidden()

        archived = CombatSession("Boss Fight", kind="boss")
        archived.mode = "heroic"
        host._browse_sessions = [archived]
        host._selected_history_idx = 1
        host._get_displayed_session = lambda: archived
        host._refresh_combat_mode_badge()
        assert "· HEROIC" in host.cp_mode_badge.text()
        assert "archived fight" in host.cp_mode_badge.toolTip()

        # an archive written before modes were named says NOTHING rather than
        # borrowing the zone the player happens to be standing in
        archived.mode = ""
        host._refresh_combat_mode_badge()
        assert host.cp_mode_badge.isHidden()
    finally:
        destroy(host.cp_mode_badge)
def test_every_surface_resolves_the_same_mode():
    """One model, three surfaces, one answer - the drift this class of bug is."""
    for mode in (None, "normal", "hard", "heroic"):
        model = _Model_(mode, inside=True)
        expected = dst.resolved_mode(model)
        ov = SpeedrunOverlay.__new__(SpeedrunOverlay)
        ov.model = model
        assert ov._resolve_mode() == expected          # Run Timer
        assert dst.instance_mode(model) == expected    # DPS HUD + DPS Analysis

# ========================================================================
# from tests/test_rift_ding.py
# ========================================================================

"""Rift alert chime scheduler (headless).

Drives RiftSidebarMixin._check_rift_ding with fake timer ticks (monkeypatched
`time.time`) and records `play_sound` calls. Verifies the once-per-lead-time-
per-rift contract: exactly one chime per selected lead time per hour, never a
duplicate in the same second, and never a burst when a later tick catches up
past several thresholds (sleep / busy UI / opening mid-warning).

Restored 2026-09-17: the v0.3.x original (written against control_panel, since
split out to ui/rift_box.py) was deleted in a test cleanup, leaving the
contract unguarded.
"""
class _S:
    def __init__(self):
        self.show_rift_timer = "Always"
        self.rift_ding = [15, 10, 5, 1]
        self.rift_sound = "ding"
class _Panel:
    """Minimal stand-in exposing what _check_rift_ding touches."""

    def __init__(self):
        self.s = _S()
        self._log_lines = []

    def log(self, msg: str) -> None:
        self._log_lines.append(msg)
class _Clock:
    """Mutable clock: `now` can be advanced between ticks."""

    def __init__(self):
        # Anchor "now" just inside a warning window: target = next top of hour.
        hour = int(time.time() // 3600)
        self.target = (hour + 1) * 3600
        self.now = self.target - 900 + 0.2   # :45:00.2, 15 min remaining

    def tick_to(self, secs_remaining: int) -> None:
        """Move the wall clock to (target - remaining) + a hair inside that
        second, matching how the sidebar sees whole-second countdowns."""
        self.now = self.target - secs_remaining + 0.2
@pytest.fixture()
def _env(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(rb_mod.time, "time", lambda: clock.now)
    played: list[str] = []
    monkeypatch.setattr(rb_mod, "play_sound", lambda name="ding": played.append(name))
    return clock, played
def _check(p: _Panel, secs: int, state: str = "WARNING") -> None:
    RiftSidebarMixin._check_rift_ding(p, {"state": state, "secs_until": secs})
def _default_panel() -> _Panel:
    p = _Panel()
    p.s.rift_ding = ["15", "10", "5", "1"]
    return p
def test_disabled_timer_never_dings(_env):
    p = _default_panel()
    p.s.show_rift_timer = "Off"
    _check(p, 899)
    _check(p, 1)
    assert _env[1] == []
def test_full_window_rings_each_threshold_once(_env):
    """Continuous ticking across one warning: four chimes, one per lead time,
    each at its own moment — never two for the same threshold."""
    clock, played = _env
    p = _default_panel()
    # Tick every second from the 15m mark down to the final second.
    for secs in range(900, 0, -1):
        clock.tick_to(secs)
        _check(p, secs)
    # Exactly one per lead time (no duplicates), and the "Live" (0) is off.
    assert len(played) == 4
    assert played == ["ding"] * 4
    # Same hour, again: nothing re-rings.
    clock.tick_to(300)
    _check(p, 300)
    assert len(played) == 4
def test_next_hour_reams_and_rings_once_each(_env):
    """Each hourly window re-arms: the same thresholds ring again, once each."""
    clock, played = _env
    p = _default_panel()
    for secs in range(900, 0, -1):
        clock.tick_to(secs)
        _check(p, secs)
    assert len(played) == 4

    # Next hour: target moves forward one hour.
    clock.target += 3600
    before = len(played)
    for secs in range(900, 0, -1):
        clock.tick_to(secs)
        _check(p, secs)
    assert len(played) - before == 4
def test_duplicate_eval_in_same_second_never_doubles(_env):
    """A second refresh evaluating the same second (timer + nav sync) must not
    ring a threshold twice."""
    clock, played = _env
    p = _default_panel()
    clock.tick_to(899)
    _check(p, 899)          # 15m moment, rings once
    _check(p, 899)          # same second re-evaluated -> latched
    _check(p, 898)          # a second later -> still latched
    assert len(played) == 1
def test_open_mid_warning_does_not_replay_past_chimes(_env):
    """Opening with 2 minutes left must not replay the 15m/10m/5m chimes —
    only the still-pending 1m chime rings when it is due."""
    clock, played = _env
    p = _default_panel()
    clock.tick_to(120)
    _check(p, 120)
    assert played == []

    clock.tick_to(59)
    _check(p, 59)           # 1m chime rings now
    assert len(played) == 1
    clock.tick_to(58)
    _check(p, 58)           # latched, no replay
    assert len(played) == 1
def test_open_at_exact_threshold_boundary_fires_once(_env):
    """Opening exactly as the 5m threshold hits rings it once and only once."""
    clock, played = _env
    p = _default_panel()
    clock.tick_to(300)
    _check(p, 300)
    assert len(played) == 1     # the 5m chime (15m/10m latched as missed)
    clock.tick_to(299)
    _check(p, 299)
    assert len(played) == 1     # no duplicate on the next tick
def test_live_chime_rings_only_in_final_second(_env):
    """The special 0 / Live threshold rings exactly once, in the last second."""
    clock, played = _env
    p = _default_panel()
    p.s.rift_ding = [0]
    for secs in range(60, 1, -1):
        clock.tick_to(secs)
        _check(p, secs)
    assert played == []
    clock.tick_to(1)
    _check(p, 1)
    _check(p, 1)            # second eval in the same second
    assert len(played) == 1
def test_catch_up_after_gap_never_bursts(_env):
    """A long gap (sleep / busy UI) that skips several thresholds must not
    replay all of them at once on the next tick."""
    clock, played = _env
    p = _default_panel()
    clock.tick_to(900)
    _check(p, 900)          # 15m rings on time
    # Sleep past the 10m and 5m moments, waking with ~4 minutes left.
    clock.tick_to(240)
    _check(p, 240)          # catch-up: missed thresholds latched silently
    clock.tick_to(59)
    _check(p, 59)           # only the 1m chime is still pending
    assert len(played) == 2
def test_stale_countdown_neither_bursts_nor_disarms(_env):
    """A stale/small countdown against an early wall clock rings only the lead
    time whose moment the clock is actually at — and must not disarm the rest:
    unreached moments stay armed, so the stream still rings each threshold at
    its own moment (compare the catch-up case, where passed moments latch)."""
    clock, played = _env
    p = _default_panel()
    # Wall clock sits at the 15m mark, but the summary hands us a stale
    # 30-second countdown (clock step / stale snapshot).
    clock.tick_to(900)
    _check(p, 30)
    # Only the clock-correct 15m chime rings — never 10m + 5m + 1m with it.
    assert len(played) == 1
    # 10m/5m/1m were never due, so they still ring at their own moments.
    for secs in range(899, 0, -1):
        clock.tick_to(secs)
        _check(p, secs)
    assert len(played) == 4
def _all_five() -> _Panel:
    """Every lead-time chip selected, including the Live one."""
    p = _Panel()
    p.s.rift_ding = ["15", "10", "5", "1", "0"]
    return p
def test_app_opened_late_never_replays_passed_lead_times(_env):
    """Starting with 2 minutes left and all five chips on must ring ONLY the
    lead times still ahead of the rift (1m, then Live) — never 15m/10m/5m.

    This is the reported bug: the pre-window rule rang every lead time whose
    moment had already passed, so opening the app inside the warning window
    produced a burst of alarms for times that were already gone.
    """
    clock, played = _env
    p = _all_five()
    clock.tick_to(120)
    _check(p, 120)
    assert played == [], "past lead times must not ring on the first tick"

    per_tick = []
    for secs in range(119, 0, -1):
        clock.tick_to(secs)
        before = len(played)
        _check(p, secs)
        per_tick.append(len(played) - before)
    assert len(played) == 2, f"only 1m + Live were pending, got {len(played)}"
    assert max(per_tick) <= 1, "two chimes landed in the same tick"
    assert all("1m" in ln or "Live" in ln for ln in p._log_lines), p._log_lines
def test_a_full_window_with_every_chip_rings_them_one_at_a_time(_env):
    """With all five selected, a full warning window gives five chimes, each
    in its own tick, each on its own threshold — the shape the sidebar is
    supposed to have."""
    clock, played = _env
    p = _all_five()
    per_tick = []
    for secs in range(900, 0, -1):
        clock.tick_to(secs)
        before = len(played)
        _check(p, secs)
        per_tick.append(len(played) - before)
    assert len(played) == 5, played
    assert max(per_tick) == 1, "a tick never carries two chimes"
    assert [ln.split(":")[1].strip().split()[0] for ln in p._log_lines] == [
        "15m", "10m", "5m", "1m", "Live"]
def test_no_start_second_of_the_window_can_burst(_env):
    """Sweep the whole warning window as a possible app-start second (all five
    chips on): no tick may ever carry two chimes, and the first tick may only
    ring the lead time whose moment is actually now.
    """
    clock, played = _env
    moments = {1, 60, 300, 600, 900}        # the dues of 15/10/5/1/Live
    for start in range(900, 0, -37):        # every 37th second, whole window
        p = _all_five()
        clock.tick_to(start)
        before = len(played)
        _check(p, start)
        first = len(played) - before
        at_a_moment = any(d - 2 <= start <= d + 1 for d in moments)
        assert first <= 1, f"burst on the first tick when starting at {start}"
        if not at_a_moment:
            # A launch mid-warning rings NOTHING: every lead time whose moment
            # has already gone by stays silent (the reported "all five" bug).
            assert first == 0, (
                f"starting at {start} replayed a lead time that already passed")
        for secs in range(start - 1, 0, -1):
            clock.tick_to(secs)
            before = len(played)
            _check(p, secs)
            assert len(played) - before <= 1, \
                f"two chimes in one tick (start {start}, secs {secs})"
def test_chime_log_line_carries_inputs(_env):
    """Each ring logs its threshold plus the raw countdown, so a real-world
    burst can be diagnosed from the Log tab."""
    clock, played = _env
    p = _default_panel()
    clock.tick_to(300)
    _check(p, 300)
    assert len(played) == 1
    assert len(p._log_lines) == 1
    line = p._log_lines[0]
    assert "5m" in line and "remaining=300s" in line

# ========================================================================
# from tests/test_skills_section.py
# ========================================================================

"""Skills subsystem: focus-bar UI section and description resolver."""
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
def _data_present() -> bool:
    try:
        return paths.item_drops_path().exists()
    except Exception:
        return False
_SECTION_MARK = pytest.mark.skipif(
    not _data_present(),
    reason="requires bundled data (assets/data/item_drops.json)")
_KEEP: list = []
def _flush() -> None:
    """Let layout + deferred (singleShot 0) work run."""
    for _ in range(3):
        QtWidgets.QApplication.processEvents()
@_SECTION_MARK
def test_skillbar_focus_shows_full_description():
    # a real weapon whose skills include a long description
    ws = idata.weapon_skills("Mace_Benediction")
    long_skill = next((s for s in ws
                       if len(s.get("description") or "") > 150), None)
    assert long_skill is not None, \
        "Mace_Benediction should carry a long description"
    win = QtWidgets.QWidget()
    win.setFixedSize(500, 800)
    lay = QtWidgets.QVBoxLayout(win)
    bar = SkillBar(ws, set(), on_focus_changed=lambda: None)
    lay.addWidget(bar)
    win.show()
    _flush()
    bar.focus_sid(long_skill["id"])
    _flush()
    _KEEP.append(win)
    # the full description renders verbatim — never clamped or truncated
    labels = [w for w in win.findChildren(QtWidgets.QLabel)
              if w.text() == long_skill["description"]]
    assert labels, "the focused skill's full description should render"
    # no 2-line cap: default (unbounded) maximum height
    assert labels[0].maximumHeight() > 1000
def _chain_bar():
    """A SkillBar focused on Bow_BigGame's merged base chain (4 hits)."""
    ws = idata.weapon_skills("Bow_BigGame")
    base_id = next(s["id"] for s in ws
                   if s.get("type") == "Base Attack")
    win = QtWidgets.QWidget()
    win.setFixedSize(500, 800)
    lay = QtWidgets.QVBoxLayout(win)
    bar = SkillBar(ws, set(), on_focus_changed=lambda: None)
    lay.addWidget(bar)
    win.show()
    _flush()
    bar.focus_sid(base_id)
    _flush()
    _KEEP.append(win)
    return bar, win
@_SECTION_MARK
def test_chain_block_clamps_to_first_two_hits():
    bar, win = _chain_bar()
    blocks = win.findChildren(_ChainBlock)
    assert blocks, "the focused base chain should render a chain block"
    b = blocks[0]
    assert len(b._rows) == 4
    visible = [r for r in b._rows if r.isVisible()]
    assert len(visible) == 2, "only the first two hits visible by default"
    assert b._more.isVisible()
    assert "show all 4 hits" in b._more.text()
@_SECTION_MARK
def test_chain_show_all_expands_and_recollapses():
    bar, win = _chain_bar()
    b = win.findChildren(_ChainBlock)[0]
    b._more.clicked.emit()
    _flush()
    assert sum(1 for r in b._rows if r.isVisible()) == 4
    assert "show fewer" in b._more.text()
    b._more.clicked.emit()
    _flush()
    assert sum(1 for r in b._rows if r.isVisible()) == 2
    assert "show all 4 hits" in b._more.text()
@_SECTION_MARK
def test_two_hit_chain_shows_no_toggle():
    # a 2-hit chain is fully visible — no toggle ever
    b = _ChainBlock([{"name": "Swing"}, {"name": "Slam"}], None)
    win = QtWidgets.QWidget()
    win.setFixedSize(400, 400)
    lay = QtWidgets.QVBoxLayout(win)
    lay.addWidget(b)
    win.show()
    _flush()
    _KEEP.append(win)
    assert all(r.isVisible() for r in b._rows)
    assert not b._more.isVisible()
def _skill_data_present() -> bool:
    try:
        return (paths.sheets_dir() / "skill.json").exists()
    except Exception:
        return False
_SECTION_MARK_1 = pytest.mark.skipif(
    not _skill_data_present(),
    reason="requires bundled data (assets/data/skill.json)")
@_SECTION_MARK_1
def test_var_tokens_and_ref_duration():
    # Bonethrow: vars.var1 = 0.4 -> 40%, vars.var3 = 2 -> '2', and the
    # bleed's duration comes from the referenced status row (8s). ::dmg::
    # has no numeric source -> the honest 'damage' fallback.
    d = skills.skill_description("Axe_Boomerang_Skill1")
    assert d == ("Throws a sharpen bone at an enemy, dealing damage and "
                 "causing them to bleed for 40% of the damage dealt over "
                 "8s. The bone then jumps to 2 enemies.")
@_SECTION_MARK_1
def test_ref_name_and_placeholders_never_invent_numbers():
    # Mace's light burst consumes ::ref_name:: (the 'Benediction' status)
    # and ::ref_stacks:: (no value anywhere) — the unresolvable slots stay
    # neutral 'X stacks' / 'X%' instead of a fabricated number.
    d = skills.skill_description("Mace_Benediction_Skill1")
    assert "consuming all Benediction stacks" in d
    assert "If X stacks are consumed" in d
    assert "your Critical Chance and Fervor by X% for 15s" in d
@_SECTION_MARK_1
def test_bracket_stat_references():
    # [CritChance] -> 'Critical Chance'; ranges carry the sheet-implicit
    # unit (40m); small vars render as percents (0.03 -> 3%).
    d = skills.skill_description("Axe_Boomerang_Skill_Passive")
    assert d == ("Increases the Critical Chance of all allies within 40m "
                 "by 3%.")
    # [Attack] / [ComboAttack] resolve too
    d = skills.skill_description("PhysicalBlock")
    assert "Blocks incoming Attacks" in d
    assert "your next Attack by 30%" in d      # ref_damage% -> the status row
@_SECTION_MARK_1
def test_missing_values_stay_honest():
    # ::val1%:: has no source in PhysicalBlock's row or its ref -> 'X%',
    # never a made-up percentage. The resolved 15% comes from a REAL var.
    d = skills.skill_description("PhysicalBlock")
    assert "reducing their damage by X%" in d
    assert "by an additional 15%" in d
@_SECTION_MARK_1
def test_basic_attack_without_vars():
    # no vars -> 'damage'; no leftover template tokens
    d = skills.skill_description("Axe_Base_Attack")
    assert d == "Deals damage to enemies in a small cone."
    assert "::" not in d
@_SECTION_MARK_1
def test_ref_slot_with_no_ref_target():
    # GS_Nova_Ultimate's desc references ::ref2_dmg:: but carries no refs —
    # the noun fallback keeps the sentence readable.
    d = skills.skill_description("GS_Nova_Ultimate")
    assert d == ("Smashes the ground and releases a massive nova dealing "
                 "damage to all enemies and knocking them back.")
@_SECTION_MARK_1
def test_no_leftover_template_tokens_anywhere():
    """Property: every description in the sheet resolves without a single
    '::' or '[' left behind (520 rows) — and nothing crashes on ref cycles."""
    rows = skills._rows()
    assert rows, "skill sheet should load"
    n = 0
    for sid, row in rows.items():
        raw = (row.get("texts") or {}).get("desc")
        if not raw:
            continue
        out = skills.skill_description(sid)
        assert out, sid
        assert "::" not in out, sid
        assert "[" not in out, sid
        n += 1
    assert n > 400          # the vast majority of rows carry descriptions
@_SECTION_MARK_1
def test_resolves_from_compiled_shim_without_loose_sheet():
    """Frozen-build path: the loose skill.json is a compile input only, so
    the compiler carries the resolver fields (texts/vars/cooldown/duration/
    steps) in the raw_skills shim. With the loose sheet hidden, descriptions
    must still resolve from the shim alone — the item page's weapon-skills
    section depends on this in the frozen app."""
    from pathlib import Path

    class NoSheets:
        def sheets_dir(self):
            return Path(__file__).resolve().parents[1] / "tmp_preview"

    orig = skills.paths
    try:
        skills.paths = NoSheets()
        skills._rows.cache_clear()
        assert skills.skill_description("Axe_Boomerang_Skill1") == (
            "Throws a sharpen bone at an enemy, dealing damage and causing "
            "them to bleed for 40% of the damage dealt over 8s. The bone "
            "then jumps to 2 enemies.")
        # rank text rides the shim too — rank 3 falls back to the base
        # description with the rankOverride value applied (3 enemies)
        assert "jumps to 3 enemies" in skills.skill_description(
            "Axe_Boomerang_Skill1", rank=3)
        assert skills.skill_type("Axe_Boomerang_Skill_Passive") == "Passive"
        # the lunge step rides the shim too (compiler keeps range +
        # duration) — the base-chain wind-up / reach lines work frozen
        assert skills.skill_moves("Axe_Base_Attack3") == \
            {"duration": 0.38, "range": 1.3}
        assert skills.skill_moves("Staff_Base_Attack") is None
        # the meta line rides the shim too (cooldown scalar + step ranges)
        assert skills.skill_meta("Axe_Boomerang_Skill1") == \
            {"cooldown": 15, "range": 40}
        assert skills.skill_meta("Axe_Boomerang_Skill_Passive") == \
            {"range": 40}
        # the whole sheet still resolves token-free from the shim
        for sid, row in skills._rows().items():
            raw = (row.get("texts") or {}).get("desc")
            if not raw:
                continue
            out = skills.skill_description(sid)
            assert out, sid
            assert "::" not in out, sid
            assert "[" not in out, sid
    finally:
        skills.paths = orig
        skills._rows.cache_clear()
@_SECTION_MARK_1
def test_rank_description_uses_rank_values():
    # rank 0 = the base description with base values (unchanged default).
    # rank N = the per-rank description (rankDescs[N-1]); ranks without a
    # per-rank line fall back to the base description — but always with
    # props.rankOverride applied (minRank <= rank), so the base text
    # reflects the rank: Bonethrow jumps to 3 enemies at rank 3 (var3
    # overridden from 2), and its rank-2 line resolves 20% (var2 = 0.2).
    d = skills.skill_description("Axe_Boomerang_Skill1")
    assert "jumps to 2 enemies" in d
    assert skills.skill_description("Axe_Boomerang_Skill1", rank=1) == \
        "Can jump to 2 enemies."
    assert skills.skill_description("Axe_Boomerang_Skill1", rank=2) == \
        "Deals 20% increased critical damage."
    d3 = skills.skill_description("Axe_Boomerang_Skill1", rank=3)
    assert d3 == d.replace("jumps to 2 enemies", "jumps to 3 enemies")
    # rank 1 applies no overrides (minRank 2+); the passive's base 3%
    # becomes 5% from rank 2 on
    assert skills.skill_description(
        "Axe_Boomerang_Skill_Passive", rank=1) == \
        "Critical Chance bonus increased to 3%."
    assert "by 5%" in skills.skill_description(
        "Axe_Boomerang_Skill_Passive", rank=3)
@_SECTION_MARK_1
def test_rank_descriptions_resolved_lines():
    # skill_rank_descriptions returns every rank line resolved at its own
    # rank, in order; rows without rankDescs return []
    assert skills.skill_rank_descriptions("Axe_Boomerang_Skill_Passive") == [
        "Critical Chance bonus increased to 3%.",
        "When you perform a Physical critical strike, heal yourself for "
        "10% of your Max Health (6s cooldown).",
    ]
    assert skills.skill_rank_descriptions("Axe_Base_Attack") == []
    assert skills.skill_rank_descriptions("NoSuchSkill") == []
@_SECTION_MARK_1
def test_rank_props_override_and_placeholder_honesty():
    # rank lines resolve like the base: Daggers_Start_Skill1's rank-2 line
    # carries ::dmg:: (no source -> the honest 'damage' noun, never an
    # invented number) and ::var1:: from the row's vars; its rank-1 line
    # resolves ::chance:: (0.15 -> 15%)
    assert skills.skill_description(
        "Daggers_Start_Skill1", rank=2) == "Now deals damage 2 times."
    d1 = skills.skill_description("Daggers_Start_Skill1", rank=1)
    assert "15% additional chance to critically strike" in d1
    assert "::" not in d1 and "[" not in d1
    # unresolvable slots in rank lines stay neutral (never invented numbers)
    assert "X% Critical Chance" in skills.skill_description(
        "Axe_Boomerang_Combo", rank=1)
@_SECTION_MARK_1
def test_no_leftover_rank_tokens_anywhere():
    """Property: every rank line in the sheet resolves without a single
    '::' or '[' left behind (256 lines across 128 rows)."""
    rows = skills._rows()
    n = 0
    for sid, row in rows.items():
        for i, rd in enumerate((row.get("texts") or {}).get("rankDescs")
                               or []):
            if not rd.get("desc"):
                continue
            out = skills.skill_description(sid, rank=i + 1)
            assert out, sid
            assert "::" not in out, (sid, i)
            assert "[" not in out, (sid, i)
            n += 1
    # 272 with the Nightling (Demon) infusion skills joining the
    # describable set (268 was the refresh that added Crimson's, 265 the
    # first Infusion_* pass)
    #
    # This assertion belongs OUTSIDE the loop above. It used to sit at the
    # inner `for`'s indentation, so it ran after the FIRST row — where n is
    # still 0 — and failed with 'assert 0 == 272' on every row that has no
    # rank lines. It read as a data regression (the shim "lost" rankDescs)
    # when it was a plain indentation slip that could never have passed.
    assert n == 272
@_SECTION_MARK_1
def test_skill_moves_real_step_data():
    """Base-attack hits carry their lunge step's real numbers: the move
    step's duration (wind-up) and range (reach) — meters/seconds, the same
    units the resolver appends to ranges and durations. Skills without a
    move step (projectiles, ranged casts) return None."""
    assert skills.skill_moves("Axe_Base_Attack") == \
        {"duration": 0.11, "range": 0.9}
    assert skills.skill_moves("Axe_Base_Attack2") == \
        {"duration": 0.14, "range": 0.8}
    assert skills.skill_moves("Axe_Base_Attack3") == \
        {"duration": 0.38, "range": 1.3}
    # ranged cast (staff) and projectile (Bonethrow): no lunge to show
    assert skills.skill_moves("Staff_Base_Attack") is None
    assert skills.skill_moves("Axe_Boomerang_Skill1") is None
    assert skills.skill_moves("NoSuchSkill") is None
@_SECTION_MARK_1
def test_skill_meta_real_stats():
    """Non-base skills expose their cooldown / range where the sheet has
    them — the SAME values the ::cooldown:: / ::range:: slots resolve to,
    so the meta line never disagrees with the description."""
    # Bonethrow: 15s cooldown (real sheet field) + the bouncer's 40m range
    assert skills.skill_meta("Axe_Boomerang_Skill1") == \
        {"cooldown": 15, "range": 40}
    # Bloodrage's 40m comes from vars.range (matches its description text)
    assert skills.skill_meta("Axe_Boomerang_Skill_Passive") == \
        {"range": 40}
    assert skills.skill_meta("Axe_Boomerang_Combo") == {"range": 6}
    assert skills.skill_meta("ShieldBlock") == \
        {"cooldown": 0.1, "range": 0.2}
    # step ranges can be template strings — never surfaced as numbers
    assert skills.skill_meta("NoSuchSkill") == {}
@_SECTION_MARK_1
def test_unit_slots_never_percent_formatted():
    """Sub-1 values in unit-bearing slots are QUANTITIES with their
    unit, never the percent heuristic: ::time2:: = 0.25 reads '0.25s',
    not '25%s'. Ratio slots (::chance::, ::dmg::) keep percent
    formatting."""
    assert "every 0.25s" in skills.skill_description("GS_Nova_Skill1")
    assert "reduces the cooldown by 0.25s" in skills.skill_description(
        "GS_Nova_Skill1", rank=2)
    assert "by 0.5s" in skills.skill_description(
        "Sword_Start_Combo", rank=2)
    assert "by 0.35s" in skills.skill_description(
        "Spear_Eruption_Skill1", rank=2)
    # ratio slots still read as percents
    assert "15% additional chance" in skills.skill_description(
        "Daggers_Start_Skill1", rank=1)
    assert "40% of the damage dealt" in skills.skill_description(
        "Axe_Boomerang_Skill1")
    # property: no '%s' / '%m' unit artifact anywhere in the sheet
    import re
    for sid, row in skills._rows().items():
        t = (row.get("texts") or {})
        if t.get("desc"):
            assert not re.search(r"%[sm]\b",
                                 skills.skill_description(sid) or ""), sid
        for i, rd in enumerate(t.get("rankDescs") or []):
            if rd.get("desc"):
                assert not re.search(
                    r"%[sm]\b",
                    skills.skill_description(sid, rank=i + 1) or ""), (sid, i)
@_SECTION_MARK_1
def test_unknown_skill():
    assert skills.skill_description("NoSuchSkill") is None
    assert skills.skill_type("NoSuchSkill") is None
    assert skills.skill_description("NoSuchSkill", rank=2) is None
@_SECTION_MARK_1
def test_skill_type_labels():
    # nature -> the item page's type chip labels
    assert skills.skill_type("Axe_Base_Attack") == "Base Attack"
    assert skills.skill_type("Axe_Boomerang_Combo") == "Combo"
    assert skills.skill_type("Axe_Boomerang_Skill1") == "Active"
    assert skills.skill_type("Axe_Boomerang_Skill_Passive") == "Passive"
    # boss AoE hit-zone rows (nature 6) are never player skills
    assert skills.skill_type("MChuck_CheeseCatapultArea") is None
