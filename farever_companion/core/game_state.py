"""One snapshot of the game's state per tick, read once and shared.

Several overlays and rules ask the same handful of questions on every frame -
is an instance up, is it a rift, which boss is this, is a menu covering the HUD,
what zone is loaded - and every one of those questions is a walk over game
memory (the UI window list, GameLayer, the activity system, the rift
scheduler). Asked independently they cost the sum of those walks per frame, and
- worse - they can *disagree* inside a single tick, which is exactly how the
overlay rules and the cursor gate drifted apart in the first place.

`OverlayManager._combat_tick` answers all of them once per tick and publishes
the result on the model (`LiveModel.publish_state`). Every other reader goes
through the accessors at the bottom of this module, so a tick has ONE answer to
each question. A model with no published snapshot - a duck-typed test double,
or a read between detach and the next tick - falls back to its own live read,
which is what the old call sites did.

The flags in a snapshot are the EFFECTIVE ones: an unknown is held at its last
proven value (see `Held`), so a failed read can never make the HUD, the cursor
or the DPS attribution change its mind. The raw tri-states are kept alongside
(`menu`, `menu_gameplay`) for the one caller that must not act on a stale answer
- the mouse recentre.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class Held:
    """Holds the last PROVEN value of a tri-state game read.

    `update(state)` returns what the caller should act on: a definite True or
    False passes through and is remembered; None (the read failed) returns the
    last proven value, so "couldn't tell" means "don't change anything". The
    default is what a caller sees before the very first successful read.
    """

    __slots__ = ("value",)

    def __init__(self, default: bool = False) -> None:
        self.value = bool(default)

    def update(self, state: bool | None) -> bool:
        if state is not None:
            self.value = bool(state)
        return self.value

    def reset(self, default: bool = False) -> None:
        self.value = bool(default)


@dataclass(frozen=True)
class GameState:
    """One tick's answers, immutable, shared by every overlay and rule."""

    tick: int = 0
    located: bool = False
    menu: bool | None = None              # this tick's read of the window list
    menu_gameplay: bool | None = None     # ...gameplay windows only (Rule B)
    held_menu: bool = False               # last proven; what the rules act on
    held_menu_gameplay: bool = False
    in_dungeon: bool = False
    in_rift: bool = False
    instance_known: bool = False          # False = the scene read failed
    map_id: str | None = None
    dungeon_boss: str | None = None
    rift: Any = None                      # RiftStatus, when the model has one

    @property
    def in_instance(self) -> bool:
        return self.in_dungeon or self.in_rift


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _located(m) -> bool:
    return _safe(lambda: getattr(m, "player_addr", None)) is not None


def read_instance(m) -> tuple[bool, bool, bool]:
    """`(in_dungeon, in_rift, known)` - `known` False means the scene read failed.

    Prefers the model's tri-state (`LiveModel.instance_state`, which reports
    None rather than guessing) and accepts the boolean pair from a duck-typed
    model, where a raise is the only way a read fails.
    """
    reader = getattr(m, "instance_state", None)
    if callable(reader):
        pair = _safe(reader)
        if pair is None:
            return False, False, False
        return bool(pair[0]), bool(pair[1]), True
    pair = _safe(lambda: (bool(m.is_in_dungeon()), bool(m.is_in_rift())))
    if pair is None:
        return False, False, False
    return pair[0], pair[1], True


def _rift_of(m):
    return _safe(lambda: m.rift_status())


def read_game_state(m, *, tick: int = 0, menu: bool | None = None,
                    menu_gameplay: bool | None = None,
                    held_menu: bool | None = None,
                    held_menu_gameplay: bool | None = None,
                    instance: tuple[bool, bool, bool] | None = None,
                    map_id: str | None = None) -> GameState:
    """Pack one tick's answers, reading the parts only this builder wants.

    The caller passes what it already knows (and has held); `dungeon_boss` is an
    attribute the unit scan maintains, and `rift_status()` is called here so the
    overlays that all want it share one call per tick instead of one each. When
    the held menu flags aren't supplied they're taken to equal this tick's read.
    """
    in_dungeon, in_rift, known = instance if instance is not None else read_instance(m)
    return GameState(
        tick=tick,
        located=_located(m),
        menu=menu,
        menu_gameplay=menu_gameplay,
        held_menu=bool(menu) if held_menu is None else bool(held_menu),
        held_menu_gameplay=(bool(menu_gameplay) if held_menu_gameplay is None
                            else bool(held_menu_gameplay)),
        in_dungeon=in_dungeon,
        in_rift=in_rift,
        instance_known=known,
        map_id=map_id,
        dungeon_boss=_safe(lambda: getattr(m, "dungeon_boss", None)),
        rift=_rift_of(m),
    )


def _instance_gate_line(m, st) -> str:
    """One Activity-Log line explaining the instance gate's verdict.

    A closed gate hides the Dungeon HUD outright and empties the instance
    overlays, while `in_dungeon()` / `is_rift()` fold every read failure into
    False - so without this the failure is silent. The raw inputs behind the
    verdict (activity class + super-chain, map id, rift byte, config offset) are
    the only way to tell a renamed activity class from an empty mainActivity
    slot or an unrecognised map id. See ``Scene.instance_probe``.
    """
    try:
        p = m.scene.instance_probe(m.player_addr)
    except Exception:
        p = {}
    where = p.get("map_id") or p.get("activity_id") or "no map id"
    off = p.get("activity_off")
    slot = f"0x{off:x}" if isinstance(off, int) else "none"
    via = p.get("activity_via") or "?"
    # The config slot moved on the 2026-09-19 build; naming the resolved slot and
    # its declared type is what makes map_id/difficulty failures visible.
    coff = p.get("cfg_off")
    cfg = (f"{p.get('cfg_type') or 'no type'} @ "
           + (f"0x{coff:x}" if isinstance(coff, int) else "not found"))
    if st.in_instance:
        kind = "rift" if st.in_rift and not st.in_dungeon else "dungeon"
        return (f"Dungeon gate OPEN ({kind}) — {where} · activity "
                f"{p.get('activity_class') or 'none'} @ {slot}/{via} · "
                f"config {cfg}")
    chain = ",".join(p.get("activity_chain") or [])
    if not p.get("gamelayer"):
        reason = "mainActivity slot empty/unreadable"
    elif off is None and p.get("activity_via") == "empty":
        # The slot was FOUND and is NULL - the overworld between activities,
        # which is where a closed gate is correct. It used to fall into the
        # branch below and claim an offset may have moved, on every walk
        # outside an instance.
        reason = "mainActivity slot empty (no activity - you are outside one)"
    elif off is None:
        reason = "mainActivity slot not found (offset moved?)"
    else:
        reason = "activity class not instance-like"
    return (f"Dungeon gate closed — {reason}; the Dungeon HUD stays hidden. "
            f"map={where} · activity={p.get('activity_class') or 'none'} @ "
            f"{slot}/{via} · chain={chain or 'none'} · "
            f"rift_byte={p.get('rift_byte')} · config {cfg}")


class TickReads:
    """The per-tick snapshot owner: holds the proven flags, builds and publishes.

    One instance lives on `OverlayManager`. Every tick it folds that tick's raw
    reads into the held flags, packs them (plus the boss, the rift schedule and
    the zone) into a `GameState`, and hands it to the model so the rest of the
    app reads the same object until the next tick.

    ``log_line`` (optional) receives one line per instance-gate transition, so
    a gate that stays shut explains itself instead of silently hiding the
    Dungeon HUD.
    """

    def __init__(self, log_line=None) -> None:
        self.tick = 0
        self._log_line = log_line
        self._in_instance_prev: bool | None = None   # last announced verdict
        self._menu_any = Held()
        self._menu_hide = Held()
        self._in_dungeon = Held()
        self._in_rift = Held()

    def read(self, m, *, menu: bool | None, menu_gameplay: bool | None,
             instance: tuple[bool, bool, bool] | None = None,
             map_id: str | None = None) -> GameState:
        in_dungeon, in_rift, known = (instance if instance is not None
                                      else read_instance(m))
        self.tick += 1
        state = read_game_state(
            m,
            tick=self.tick,
            menu=menu,
            menu_gameplay=menu_gameplay,
            held_menu=self._menu_any.update(menu),
            held_menu_gameplay=self._menu_hide.update(menu_gameplay),
            instance=(self._in_dungeon.update(in_dungeon if known else None),
                      self._in_rift.update(in_rift if known else None),
                      known),
            map_id=map_id,
        )
        publish_state(m, state)
        self._log_gate_change(m, state)
        return state

    def _log_gate_change(self, m, state: GameState) -> None:
        """Announce the instance gate on every transition (see _instance_gate_line)."""
        if self._log_line is None or state.in_instance == self._in_instance_prev:
            return
        self._in_instance_prev = state.in_instance
        try:
            self._log_line(_instance_gate_line(m, state))
        except Exception:
            pass

    def reset(self) -> None:
        """Drop the held flags - a new session must not inherit the last one's."""
        self._in_instance_prev = None
        for h in (self._menu_any, self._menu_hide, self._in_dungeon, self._in_rift):
            h.reset()


def publish_state(m, state: GameState) -> None:
    """Hand a snapshot to the model, when it can carry one."""
    fn = getattr(m, "publish_state", None)
    if callable(fn):
        _safe(lambda: fn(state))


# --- readers ------------------------------------------------------------------
# Each prefers the published snapshot and falls back to the model's own live
# read, so overlays and rules never re-derive the answers a tick already made.
def published_state(m) -> GameState | None:
    """The snapshot a tick published, if any. Never builds one."""
    st = getattr(m, "published_state", None)
    return st if isinstance(st, GameState) else None


def _live_dungeon(m) -> bool:
    fn = getattr(m, "is_in_dungeon", None)
    return bool(_safe(fn, False)) if callable(fn) else False


def _live_rift(m) -> bool:
    fn = getattr(m, "is_in_rift", None)
    return bool(_safe(fn, False)) if callable(fn) else False


def in_dungeon(m) -> bool:
    st = published_state(m)
    return st.in_dungeon if st is not None else _live_dungeon(m)


def in_rift(m) -> bool:
    st = published_state(m)
    return st.in_rift if st is not None else _live_rift(m)


def in_instance(m) -> bool:
    """Inside any dungeon or rift instance - the DPS/tracker/overlay gate."""
    st = published_state(m)
    if st is not None:
        return st.in_instance
    fn = getattr(m, "is_in_dungeon_or_rift", None)
    if callable(fn):
        return bool(_safe(fn, False))
    # duck-typed doubles sometimes carry plain flags instead
    return bool(getattr(m, "is_dungeon", False) or getattr(m, "is_rift", False)
                or _live_dungeon(m) or _live_rift(m))


#: The three instance difficulties the game itself reports.
MODES = ("normal", "hard", "heroic")


def instance_mode(m) -> str:
    """The game's OWN difficulty read for the current instance, or ``""``.

    Deliberately NOT the manual fallback. This is what a caller uses to record
    a LASTING fact - the difficulty stamped on an archived fight and into its
    log's file name - and a guess written there could never be corrected.
    `ui/dps_source_text.resolved_mode` owns the guess for live surfaces, which
    may always show something; an archive has to be able to say "unknown".
    """
    fn = getattr(m, "detected_mode", None)
    if not callable(fn):
        return ""
    try:
        mode = fn()
    except Exception:
        return ""
    if isinstance(mode, str) and mode.lower() in MODES:
        return mode.lower()
    return ""


def boss(m) -> str | None:
    st = published_state(m)
    return st.dungeon_boss if st is not None else _safe(lambda: getattr(m, "dungeon_boss", None))


def zone(m) -> str | None:
    st = published_state(m)
    return st.map_id if st is not None else None


def rift(m):
    """The rift schedule/status. One call per tick serves every overlay."""
    st = published_state(m)
    return st.rift if st is not None else _rift_of(m)
