"""Fx-based collected-orb detection (glow fx = not collected), debounced."""
from farever_companion.geo.orb_sync import FxSync


def test_fxless_marks_after_two_ticks():
    s = FxSync()
    mark, unmark = s.update([("RedOrb_World_86", False)], set())
    assert mark == [] and unmark == []          # debounce: first sighting
    mark, unmark = s.update([("RedOrb_World_86", False)], set())
    assert mark == ["RedOrb_World_86"]          # confirmed


def test_glowing_unmarks_after_two_ticks():
    s = FxSync()
    mark, unmark = s.update([("RedOrb_World_35", True)], {"RedOrb_World_35"})
    assert mark == [] and unmark == []           # debounced: one glow sighting
    mark, unmark = s.update([("RedOrb_World_35", True)], {"RedOrb_World_35"})
    assert mark == []
    assert unmark == ["RedOrb_World_35"]        # glowing twice = not collected


def test_single_tick_glow_never_unmarks_collected():
    """A transient bad fx read (stale offset, chunk flap) must not wipe a
    collected orb from the done list: glowing once then dark keeps the mark."""
    s = FxSync()
    s.update([("RedOrb_World_35", True)], {"RedOrb_World_35"})   # one bad read
    mark, unmark = s.update([("RedOrb_World_35", False)], {"RedOrb_World_35"})
    assert mark == [] and unmark == []           # still collected
    mark, unmark = s.update([("RedOrb_World_35", False)], {"RedOrb_World_35"})
    assert mark == [] and unmark == []           # stays quiet forever after


def test_fx_spawn_lag_resets_debounce():
    s = FxSync()
    s.update([("RedOrb_World_1", False)], set())     # chunk just loaded, no fx yet
    mark, _ = s.update([("RedOrb_World_1", True)], set())
    assert mark == []
    mark, _ = s.update([("RedOrb_World_1", False)], set())
    assert mark == []                            # pending was reset by the glow


def test_unloaded_pending_dropped():
    s = FxSync()
    s.update([("RedOrb_World_2", False)], set())
    s.update([], set())                          # orb unloaded between ticks
    mark, _ = s.update([("RedOrb_World_2", False)], set())
    assert mark == []                            # must re-confirm after reload


def test_already_done_stays_quiet():
    s = FxSync()
    s.update([("RedOrb_World_9", False)], {"RedOrb_World_9"})
    mark, unmark = s.update([("RedOrb_World_9", False)], {"RedOrb_World_9"})
    assert mark == [] and unmark == []
