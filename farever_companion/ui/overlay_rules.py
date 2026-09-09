"""HUD placement rules — where each window exists, declared once.

Split out of `ui/overlay_manager.py`, which had reached the ui/ line budget
*with* these rules in it. They are a self-contained family of pure decisions
(the tick's GameState plus the tracker's last scene scan in, a hide/show
verdict out), so they are the one concern in that file that can live beside it
and be unit-tested on its own.

The dependency runs one way: `overlay_manager` imports every name here and
applies them in its visibility loop, and nothing in this module imports the
manager or any overlay back — so the overlay package's "only overlay_manager
imports us" direction still holds, and the manager's import-light startup is
unaffected.

`HUD_PLACES` is the whole of it: ONE row per HUD window saying where that window
exists at all. The loop used to carry those gates as code — a tuple for the
instance-only pair, then an `if key == "dps"`, then an `if key == "dummy"` — so
three neighbours in one list answered one question three ways: the Dungeon HUD
could not exist outside an instance, the meter could not exist outside one
*unless* a setting said otherwise *or* a dummy was in range *unless* the Test
Dummy HUD was handling it, and the dummy HUD could not exist in an instance at
all even though nothing said so until somebody hit it. A new window inherited
whichever answer its author happened to copy. Reading a row is now the only way
to answer the question, the answers sit next to each other, and
`test_every_hud_overlay_declares_where_it_exists` fails when a window (or a
stale row) has none.

The names the rest of the repo uses for these rows are kept, because they are
how the codebase talks about placement: **Rule C** is the `entity` and `map`
rows (everywhere but an instance), **Rule D** the `dungeon` and `speedrun` rows,
**Rule E** the `dps` row (its `instances_only` toggle — no dummy logic: the
Test Dummy HUD owns the bench), **Rule F** the `dummy` row. The labels still
name the behaviours; what changed is that the behaviours are data now, read by
one function, instead of four branches in the loop that each asked a slightly
different question.

Rule C used to be the one that hid: a `key in ("map", "entity")` test written
TWICE in the tick (once for app focus, once under the menu rules), because the
menus are bypassed when the companion app has focus and this gate must not be.
As a row it is stated once and the tick skips the bypass question entirely —
there is nothing to gather inside a dungeon and its own HUD replaces them.

The menu rules (A/B) are deliberately NOT rows: they answer "is a menu up?",
which is a property of the tick, not of any one window.

The places:

* INSTANCE   — inside a dungeon or a rift.
* OPEN_WORLD — the hubs and the wilds outside one.
* DUMMY      — the open world *at a training dummy in range*, deliberately
               narrower than OPEN_WORLD: the one seat a dummy test gets.

`hud_place_hidden` is the single reader the loop calls, once per key per tick.
The remaining fields are the exceptions a row earns, and each names a SETTING:

* ``card`` — the flag that must be on before the row may surface the window at
  all. The instance-only pair and the dummy HUD are built at launch and sit
  hidden until their rule fires, so a card switched off mid-session has to be
  re-checked here or zoning in would resurrect a window the user closed.
* ``instances_only`` — the meter's "Instances Only" toggle: with it on the row
  narrows to INSTANCE, bench included. The meter keeps no training-dummy seat:
  at a dummy the Test Dummy HUD is the board, so there is no pop-then-close
  handover when it surfaces.
* ``dummy_auto`` — the dummy HUD's "Auto On": off makes the row an ordinary
  overlay the user opens, which for a dummy-only window means OPEN_WORLD. The
  instance half of that row is not the toggle's to give away.
* ``rule_opens`` — whether a RULE brings this window up (the instance-only pair
  on zone-in, the dummy HUD at a dummy), as opposed to a card click. Declared
  per row rather than inferred from ``places``: `entity`/`map` are narrower than
  "everywhere" too, yet the user's own click is what shows them.

`compass` and `dcompass` are needles rather than windows and carry no row: a key
with no row has no placement opinion, which is the right answer for everything
the menu rules own on their own.
"""
from __future__ import annotations

from dataclasses import dataclass

INSTANCE = "instance"
OPEN_WORLD = "open_world"
DUMMY = "dummy"
ALL_PLACES = frozenset({INSTANCE, OPEN_WORLD})
# Everywhere but an instance: the hubs and the wilds, a training dummy included.
NOT_INSTANCE = frozenset({OPEN_WORLD, DUMMY})

# Fallback radius (metres) for the dummy rules. The live number is the setting
# (Settings -> DPS tab -> Test Dummy -> Dummy Range); this is used only when a
# settings double carries no `dps_dummy_range` (tests, headless hosts).
DUMMY_RANGE_M = 5.0


@dataclass(frozen=True)
class HudPlace:
    """Where one HUD window exists, plus the exceptions its row earns."""

    places: frozenset[str]
    card: str | None = None
    instances_only: str | None = None
    dummy_auto: str | None = None
    rule_opens: bool = False


HUD_PLACES: dict[str, HudPlace] = {
    # Dungeon/rift only, surfaced on zone-in. The card is re-checked because
    # these windows are built staggered at locate (nothing else would create
    # them on dungeon entry) and sit hidden until their rule fires.
    "dungeon": HudPlace(frozenset({INSTANCE}), card="open_overlay_dungeon",
                        rule_opens=True),
    "speedrun": HudPlace(frozenset({INSTANCE}), card="open_overlay_speedrun",
                         rule_opens=True),
    # Everywhere but an instance (Rule C): an open world to gather in and a
    # dungeon that its own HUD covers instead. `rule_opens` stays False — these
    # two are toggled from the Overlays card by hand, so a click means "show it"
    # wherever the row allows it.
    "entity": HudPlace(NOT_INSTANCE),
    "map": HudPlace(NOT_INSTANCE),
    # The meter: everywhere by default, narrowable to instances by its own
    # toggle. No dummy logic: the Test Dummy HUD owns the bench, so with
    # "Instances Only" on the meter stays in instances even at a dummy.
    "dps": HudPlace(ALL_PLACES, instances_only="dps_instances_only"),
    # The dummy HUD: the dummy bench and nowhere else — never an instance — and
    # only while Auto On lets it surface by itself.
    "dummy": HudPlace(frozenset({DUMMY}), card="open_overlay_dummy",
                      dummy_auto="dps_dummy_auto", rule_opens=True),
    # Always visible: the loop short-circuits this key before any rule, so its
    # row exists to keep the declaration complete, not to gate anything.
    "devscanner": HudPlace(ALL_PLACES),
}


def _flag(settings, name: str, default: bool) -> bool:
    """A boolean setting read defensively: a detached or headless settings
    double answers `default` rather than raising out of a 10 Hz tick."""
    try:
        return bool(getattr(settings, name, default))
    except Exception:
        return default


# --- the readers the loop uses ----------------------------------------------#
def hud_instance_only(key: str) -> bool:
    """Whether this window exists ONLY inside a dungeon/rift — the half of a
    card click that says "show it on zone-in", and nowhere before that."""
    place = HUD_PLACES.get(key)
    return place is not None and place.places == frozenset({INSTANCE})


def hud_rule_owns_showing(key: str) -> bool:
    """Whether a RULE, rather than a card click, brings this window up.

    True for the instance-only pair (on zone-in) and the dummy HUD (at a
    dummy). A card click on one of those must not flash a window the next tick
    is about to hide.
    """
    place = HUD_PLACES.get(key)
    return place is not None and place.rule_opens


def hud_allowed_places(settings, key: str) -> frozenset[str] | None:
    """The places this window may be shown in right now, or None when the key
    has no row (no opinion: the menu rules decide alone)."""
    place = HUD_PLACES.get(key)
    if place is None:
        return None
    if place.card and not _flag(settings, place.card, False):
        return frozenset()          # card off: nowhere, whatever room we are in
    allowed = set(place.places)
    if place.instances_only and _flag(settings, place.instances_only, False):
        allowed = {INSTANCE}
    if place.dummy_auto and not _flag(settings, place.dummy_auto, True):
        allowed = {OPEN_WORLD}
    return frozenset(allowed)


def hud_place_hidden(settings, key: str, *, in_instance: bool,
                     at_dummy: bool) -> bool:
    """Whether the row hides this window where the player is right now.

    THE one placement question the visibility loop asks: the player's own places
    (the instance, else the open world plus the dummy bench while one is in
    range) against the window's row. No window name appears in it — that is the
    point of the table.
    """
    allowed = hud_allowed_places(settings, key)
    if allowed is None:
        return False
    here = {INSTANCE} if in_instance else {OPEN_WORLD}
    if at_dummy and not in_instance:
        here.add(DUMMY)
    return not (allowed & here)


# --- the dummy half of the table --------------------------------------------#
def _at_training_dummy(m, settings) -> bool:
    """True when the tracker's last scene scan put a training dummy inside the
    configured radius (the Dummy Range control on the Test Dummy card).

    Reads the tracker off the passed model — a detached model or a duck-typed
    test double simply answers False, and an unreadable radius is never
    evidence of being at a dummy. The loop reads it ONCE per tick and hands the
    answer to every row, so no two rows can disagree about it.
    """
    t = getattr(m, "dps", None) if m is not None else None
    fn = getattr(t, "dummy_within", None)
    if not callable(fn):
        return False
    rng = getattr(settings, "dps_dummy_range", DUMMY_RANGE_M)
    try:
        return bool(fn(rng))
    except Exception:
        return False
