"""Pure-read player locator (no hook, no writes).

The primary locate is the scan-free GameApp anchor in appsingleton.py. This
module keeps a heap-scan fallback for unexpected build layouts:

  1. Bootstrap the ent.Hero hl_type by class-name string (name -> type_obj ->
     hl_type). The type ptr is stable, so it's cached.
  2. Scan the rw heap for objects whose first qword is that hl_type.
  3. Prefer Hero.ownerPlayer.isMe verified by Player.hero; else the hero with the
     largest live unit list (unambiguous in solo). Calibration offsets below.
"""
from __future__ import annotations

import os
import struct

from .appsingleton import GameAppLocator
from .hl import Hl, is_ptr, _HOBJ, utf16z as _utf16z
from .proc import Proc, ProcError
from .constants import TO_NAME, OFF_HEADING, OFF_POS as OFF_XYZ
from .scene import OFF_GAMELAYER, OFF_UNITS_ARR

# --- calibrate live; None => use the fallback heuristic -------------------
OFF_HERO_OWNERPLAYER: int | None = None   # ent.Hero -> st.Player
OFF_PLAYER_ISME: int | None = None        # st.Player.isMe (i32/bool)
OFF_PLAYER_HERO: int | None = None        # st.Player.hero -> ent.Hero
OFF_HERO_ISCOMBAT: int | None = 0x2a8     # ent.Hero.isInCombat (bool)

# The ent.Hero heap-scan fallback is OPT-IN only (FAREVER_HEAPSCAN=1). It scans
# the multi-GB GC heap, and when fired against a half-initialized process (the
# companion launched before the game) it ran for minutes, held the locate worker
# busy, and wedged 'locating player' forever. The GameApp anchor is the validated
# path on this build, so live locate is anchor-only; the scan stays available for
# diagnosing an unexpected build layout where the anchor never resolves.
_HEAPSCAN_FALLBACK = os.environ.get("FAREVER_HEAPSCAN", "") not in ("", "0")


class PlayerLocator:
    def __init__(self, proc: Proc, hl: Hl | None = None):
        self.proc = proc
        self.hl = hl or Hl(proc)
        self._type_ptr: int | None = None
        self.address: int | None = None
        # Primary, scan-free anchor: the GameApp singleton -> GameApp.hero. The
        # Hero heap-scan below is an opt-in fallback only (see _HEAPSCAN_FALLBACK);
        # tests that exercise it set this True explicitly.
        self.heapscan_fallback = _HEAPSCAN_FALLBACK
        self.app = GameAppLocator(proc, self.hl)

    # --- type bootstrap --------------------------------------------------
    def hero_type(self) -> int | None:
        if self._type_ptr is not None:
            return self._type_ptr
        if not self.proc.has_scan:
            raise ProcError("player locate needs the farever_native scan backend")
        name_hits = self.proc.find_bytes(_utf16z("ent.Hero"), align=2,
                                         rw_only=False, max_hits=64)
        for name_addr in name_hits:
            # hl_type_obj.name points here -> type_obj = (ptr to name_addr) - TO_NAME
            for ref in self.proc.find_qword(name_addr, rw_only=False, max_hits=64):
                type_obj = ref - TO_NAME
                # a hl_type points to type_obj at +8, with kind=HOBJ at +0
                for tref in self.proc.find_qword(type_obj, rw_only=False, max_hits=64):
                    cand = tref - 8
                    try:
                        if self.hl.i32(cand) == _HOBJ and self.hl.type_name(cand) == "ent.Hero":
                            self._type_ptr = cand
                            return cand
                    except ProcError:
                        continue
        return None

    # --- instance scan + selection --------------------------------------
    def _candidates(self) -> list[int]:
        tp = self.hero_type()
        if tp is None:
            return []
        hits = self.proc.find_qword(tp, rw_only=True, max_hits=256)
        # keep ones that are real, fully-formed heroes (valid GameLayer)
        good = []
        for h in hits:
            gl = self.hl.ptr(h + OFF_GAMELAYER)
            if gl is not None:
                good.append(h)
        return good

    def _is_me(self, hero: int) -> bool | None:
        """True/False if the isMe path is calibrated, else None (unknown)."""
        if OFF_HERO_OWNERPLAYER is None or OFF_PLAYER_ISME is None:
            return None
        player = self.hl.ptr(hero + OFF_HERO_OWNERPLAYER)
        if player is None:
            return False
        try:
            isme = self.hl.i32(player + OFF_PLAYER_ISME) == 1
        except ProcError:
            return False
        if OFF_PLAYER_HERO is not None:
            back = self.hl.ptr(player + OFF_PLAYER_HERO)
            if back != hero:
                return False
        return isme

    def _unit_count(self, hero: int) -> int:
        gl = self.hl.ptr(hero + OFF_GAMELAYER)
        if gl is None:
            return -1
        arr = self.hl.ptr(gl + OFF_UNITS_ARR)
        if arr is None:
            return -1
        try:
            return self.hl.i32(arr + 8)
        except ProcError:
            return -1

    def locate(self) -> int | None:
        # Primary: GameApp singleton -> hero (scan-free, session-stable, and
        # unambiguous - GameApp.hero IS the local player).
        try:
            hero = self.app.hero()
        except ProcError:
            hero = None
        if (hero is not None and self.hl.class_of(hero) == "ent.Hero"
                and self._in_world(hero)):
            self.address = hero
            return hero
        # No in-world hero yet. Two cases, both handled by just retrying next tick
        # (cheap): (a) anchor reachable, we're at the menu/loading and it produces
        # the hero on its own once in-world; (b) anchor not reachable yet, the game
        # is still booting (companion launched first) and the metadata isn't mapped.
        # We do NOT run the heap-scan fallback here - that scan, fired against a
        # half-initialized process, wedged 'locating player' for minutes and never
        # recovered. It's available only when explicitly opted in.
        self.address = None
        if not self.heapscan_fallback or self.app.reachable:
            return None
        # Opt-in last resort for an unexpected build where the anchor never resolves.
        cands = self._candidates()
        if not cands:
            self.address = None
            return None
        # calibrated path: the verified local player
        verified = [h for h in cands if self._is_me(h) is True]
        if verified:
            self.address = verified[0]
            return self.address
        # fallback: the hero attached to the most populated world
        self.address = max(cands, key=self._unit_count)
        return self.address

    def relocate(self) -> int | None:
        """Re-find the instance after a zone change (type stays cached)."""
        return self.locate()

    # --- in-world gate ---------------------------------------------------
    def _in_world(self, hero: int | None) -> bool:
        """A Hero counts as located only once it's attached to a live GameLayer.

        On the main menu / character-select a Hero object can already exist (the
        preview model) with NO GameLayer. Without this gate the anchor binds to
        that preview hero the instant the process is up - a 'fake attach' where
        the tools unlock but every read hits an empty scene, and it never
        recovers. Requiring the GameLayer makes 'located' flip true exactly when
        the player has loaded in; the per-tick re-resolve then follows them back
        to the menu and in again. The heap-scan fallback already required this
        (see `_candidates`); the anchor path did not."""
        if not hero:
            return False
        try:
            return self.hl.ptr(hero + OFF_GAMELAYER) is not None
        except ProcError:
            return False

    # --- reads -----------------------------------------------------------
    def live_address(self) -> int | None:
        """The current local hero, re-resolved cheaply each call so it FOLLOWS
        menu<->world and character changes with no rescan.

        When the GameApp anchor is resolved (the normal path), this re-reads
        `GameApp.hero` every call: at the main menu that field is null, so this
        returns None (and the attach watcher re-applies 'located' the moment you
        load back in). Only the heap-scan fallback build, where the anchor never
        resolves, uses the last located address, re-validated so a stale pointer
        after a zone change still degrades to None instead of reading garbage."""
        if self.app.reachable:
            hero = self.app.live_hero()
            if (hero is not None and self.hl.class_of(hero) == "ent.Hero"
                    and self._in_world(hero)):
                self.address = hero
                return hero
            self.address = None       # menu / transition: no in-world hero now
            return None
        # heap-scan fallback build: keep the last address but drop it if it's no
        # longer a live, in-world Hero (zone change freed/reused it, or back to
        # the menu where the hero has no GameLayer).
        if self.address is not None and (
                self.hl.class_of(self.address) != "ent.Hero"
                or not self._in_world(self.address)):
            self.address = None
        return self.address

    def pbase(self) -> int | None:
        return self.live_address()

    def read_xyz(self) -> tuple[float, float, float] | None:
        base = self.live_address()
        if not base:
            return None
        raw = self.proc.try_read(base + OFF_XYZ, 24)
        if raw is None:
            return None
        return struct.unpack("<ddd", raw)

    def read_heading(self) -> float | None:
        """Player facing in radians (atan2: 0=+x, CCW). Pure read of the heading
        field in the player struct; holds when still, updates while turning."""
        base = self.live_address()
        if not base:
            return None
        raw = self.proc.try_read(base + OFF_HEADING, 8)
        if raw is None:
            return None
        return struct.unpack("<d", raw)[0]

    def in_combat(self) -> bool | None:
        if not self.address or OFF_HERO_ISCOMBAT is None:
            return None
        try:
            return self.hl.i32(self.address + OFF_HERO_ISCOMBAT) != 0
        except ProcError:
            return None

    def player_profile(self) -> str | None:
        """Find the unique name_hash key of the current character using reflection."""
        hero = self.live_address()
        if not hero:
            return None
        # OFF_HERO_OWNERPLAYER = 16
        player = self.hl.ptr(hero + 16)
        if not player:
            return None
        type_ptr = self.hl.u64(player)
        if not is_ptr(type_ptr):
            return None

        # 1) Resolve name/username
        name = None
        for fn in ("name", "userName", "playerName"):
            off = self.hl.field_offset(type_ptr, fn)
            if off is not None:
                str_ptr = self.hl.ptr(player + off)
                if str_ptr:
                    name = self.hl.hl_string(str_ptr)
                    break
        if not name:
            return None

        # 2) Resolve unique database/account/character ID
        uid = None
        for fn in ("dbId", "id", "uid", "pid", "playerId", "charId"):
            off = self.hl.field_offset(type_ptr, fn)
            if off is not None:
                try:
                    # String ID check
                    val_ptr = self.hl.ptr(player + off)
                    if val_ptr and self.hl.class_of(val_ptr) == "String":
                        uid = self.hl.hl_string(val_ptr)
                        break
                    # Numeric ID check
                    val_i32 = self.hl.i32(player + off)
                    if val_i32 != 0:
                        uid = f"{val_i32 & 0xffffffff:08x}"
                        break
                except Exception:
                    continue

        if uid:
            return f"{name}_{uid}"
        return name
