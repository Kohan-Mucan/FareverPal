"""Probe-first reader for the client's server-synced party (``st.Group``).

Farever keeps the party in a net-replicated ``st.Group`` object whose
``players`` prop lists every member (each an ``st.Player`` carrying its real
character name, hero entity and uid). Reading it yields the AUTHORITATIVE
roster - who is in your group, who leads it, whether you are solo - at any
distance, unlike the local scene scan which only sees units near the player.

Two layout facts were confirmed against the live game (2026-09-04):

1. The ``players`` prop does NOT point at a plain HL array. It holds an
   ``hxbit.ArrayProxyData`` - the network-synced array proxy - whose own
   ``array`` field is the real ``hl.types.ArrayDyn`` (proven in bytecode:
   ``hxbit.ArrayProxyData.get_length`` reads this.field4 as
   ``hl.types.ArrayDyn``). ``decode`` unwraps it probe-first and never trusts
   a slot whose pointee is not a class-verified array with a sane length.
2. ``st.Group`` declares NO ``leader``/``isSolo`` fields (own fields are
   groupId, players, instanceLobbies, suggestedActivities, nextServerID);
   both are computed getters in the game's bytecode:
   ``get_isSolo`` = (players.length == 0) and ``get_leader`` = players[1].

``st.Group`` is also the object the game's "invite player to group" flow
mutates (``st.Group.invitePlayer`` -> server ``addToGroup`` -> replicated
``players`` prop), so this reader consumes the READ side of that feature:
it can surface who just joined (players grows) or who is leading, but it can
never send an invite itself - the RPC runs inside the game process and the
companion is read-only by design (no writes/injection; see
tests/test_readonly_default.py).

Like meterhud.py this is deliberately a *probe-first* reader: every field is
decoded BY NAME via ``Hl.field_offset`` (super-chain aware, no hardcoded
per-build offsets) and every pointer is class-verified before it is trusted,
so a wrong assumption degrades to an empty roster rather than a fabricated
row. The primary locate is scan-free - the local player's own
``st.Player.group`` slot points at the group - with a bounded heap scan as a
fallback for builds where that chain does not resolve.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass, field

from .damage import (
    _small_rw_ranges, find_type_by_name, cluster_ranges, clip_ranges,
)
from .hl import Hl, is_ptr
from .proc import Proc, ProcError

GROUP_CLASS = "st.Group"
PLAYER_CLASS = "st.Player"

# Reflection field-name candidates per semantic, in preference order (the
# same probe-first pattern as meterhud's _*_FIELDS tuples, so a Haxe field
# rename degrades to the next candidate instead of a hardcoded offset).
_GROUP_PLAYERS_FIELDS = ("players", "members")
_GROUP_LEADER_FIELDS = ("leader", "leaderPlayer", "leaderUid", "owner")
_PLAYER_GROUP_FIELDS = ("group",)
_PLAYER_NAME_FIELDS = ("name", "userName", "playerName", "displayName")
_PLAYER_HERO_FIELDS = ("hero", "heroUnit", "heroObj", "character", "unit")
_PLAYER_UID_FIELDS = ("uid", "id", "dbId", "playerId")
_PLAYER_ISME_FIELDS = ("isMe", "isLocal", "me")
_PLAYER_CONN_FIELDS = ("connected", "isConnected", "online")

# The players prop layout: on the live game it is hxbit.ArrayProxyData (the
# network proxy), whose OWN field is the real HL array.
PROXY_CLASS = "hxbit.ArrayProxyData"
_ARRAY_CLASSES = ("hl.types.ArrayDyn", "hl.types.ArrayObj")
_PROXY_ARRAY_FIELDS = ("array",)
_PROXY_SCAN_BYTES = 0x80     # bounded slot sweep across the proxy object

# Rate limits: the roster only changes on group joins/leaves, so a ~1 Hz
# decode is far more than enough and keeps the small scans off the hot path.
SNAPSHOT_TTL_S = 1.0      # min interval between live decodes
REMAP_EMPTY_S = 6.0       # while NO group decodes, remap the heap this often
FULL_DERIVE_S = 120.0     # rare full-RW sweep fallback (mirrors meterhud)
MAX_MEMBERS = 64          # sanity cap for the players array


@dataclass
class GroupMember:
    """One roster entry: a class-verified ``st.Player`` with best-effort
    identity fields (any unreadable slot stays 0/"" rather than guessed)."""
    player: int = 0                # st.Player heap ptr
    name: str = ""
    uid: str = ""
    hero: int = 0                  # ent.Hero heap ptr (0 when unreadable)
    is_me: bool = False
    connected: bool = True
    is_leader: bool = False


@dataclass
class GroupSnapshot:
    """A decoded party roster off ONE ``st.Group`` instance."""
    group: int = 0                 # st.Group heap ptr
    members: list[GroupMember] = field(default_factory=list)
    solo: bool = False

    def contains(self, local_player: int, local_hero: int) -> bool:
        for m in self.members:
            if local_player and m.player == local_player:
                return True
            if local_hero and m.hero == local_hero:
                return True
        return False

    @property
    def leader(self) -> GroupMember | None:
        for m in self.members:
            if m.is_leader:
                return m
        return None


class GroupReader:
    """Read the party roster off the game's replicated ``st.Group`` object.

    Poll ``snapshot(local_player, local_hero)`` (rate-limited ~1 Hz) for the
    local player's group. Nothing is cached forever: each decode re-reads the
    live ``players`` array, so joins/leaves/leader-changes appear on the next
    poll with no instance rescan needed.
    """

    def __init__(self, proc: Proc, hl: Hl):
        self.proc = proc
        self.hl = hl
        self._group_type: int | None = None
        self._group_absent = False
        self._anchor_addrs: set[int] = set()
        self._scan_ranges: list[tuple[int, int]] = []
        self._last_remap = 0.0
        self._last_full_derive = 0.0
        self._snap: GroupSnapshot | None = None
        self._snap_at = 0.0

    # --- type bootstrap --------------------------------------------------
    def find_type(self) -> int | None:
        """Locate the ``st.Group`` hl_type once (cached for the process)."""
        if self._group_type is not None or self._group_absent:
            return self._group_type
        if not self.proc.has_scan:
            self._group_absent = True
            return None
        tp = find_type_by_name(self.proc, self.hl, GROUP_CLASS)
        if tp is None:
            self._group_absent = True
            return None
        self._group_type = tp
        return tp

    @property
    def present(self) -> bool:
        """The st.Group type is in the process (may still have no instance)."""
        return self._group_type is not None

    # --- helpers ---------------------------------------------------------
    @staticmethod
    def _first_offset(hl: Hl, type_ptr: int,
                      names: tuple[str, ...]) -> int | None:
        for n in names:
            try:
                off = hl.field_offset(type_ptr, n)
            except (ProcError, OSError):
                continue
            if off:
                return off
        return None

    def _is_a(self, addr: int, ancestor: str) -> bool:
        tp = self.hl.ptr(addr)
        return tp is not None and self.hl.type_is_a(tp, ancestor)

    def _string_at(self, field_addr: int) -> str | None:
        """Read an HL String object pointer at ``field_addr``, trusted only
        when the pointee's runtime class really is String."""
        strobj = self.hl.ptr(field_addr)
        if strobj is None:
            return None
        try:
            if self.hl.class_of(strobj) != "String":
                return None
        except (ProcError, OSError):
            return None
        return self.hl.hl_string(strobj)

    def _flag_at(self, addr: int) -> bool:
        """Read a HL Bool field (1 byte at the field offset)."""
        try:
            raw = self.proc.try_read(addr, 1)
            return bool(raw and raw[0])
        except (ProcError, OSError):
            return False

    def _sane_array_len(self, arr_addr: int) -> int | None:
        """The length of a class-verified HL array, or None when the read is
        garbage (length outside 0..MAX_MEMBERS / unreadable)."""
        if not arr_addr:
            return None
        try:
            n = self.hl.i32(arr_addr + 8)
        except (ProcError, OSError):
            return None
        return n if 0 <= n <= MAX_MEMBERS else None

    def _is_array(self, addr: int) -> bool:
        if not addr:
            return False
        try:
            return self.hl.class_of(addr) in _ARRAY_CLASSES
        except (ProcError, OSError):
            return False

    def _players_array(self, players_slot_addr: int) -> int | None:
        """The real roster array behind a group's ``players`` prop slot.

        The prop may be a plain HL array OR an hxbit.ArrayProxyData (the
        live game's layout) whose own ``array`` field holds the real array
        (bytecode-proven: ``hxbit.ArrayProxyData.get_length`` reads
        this.field4 as an ``hl.types.ArrayDyn``). Unwrap probe-first:
        reflection offset for the proxy's ``array`` field, then a bounded
        slot sweep of the proxy object for a class-verified array with a
        sane length. Returns None when nothing verifies - never a pointer
        that could decode into fabricated members."""
        obj = self.hl.ptr(players_slot_addr)
        if obj is None:
            return None
        if self._is_array(obj):
            return obj if self._sane_array_len(obj) is not None else None
        try:
            if self.hl.class_of(obj) != PROXY_CLASS:
                return None
        except (ProcError, OSError):
            return None
        tp = self.hl.ptr(obj)
        if tp:
            off = self._first_offset(self.hl, tp, _PROXY_ARRAY_FIELDS)
            if off:
                a = self.hl.ptr(obj + off)
                if (a and self._is_array(a)
                        and self._sane_array_len(a) is not None):
                    return a
        try:
            raw = self.proc.try_read(obj, _PROXY_SCAN_BYTES)
        except (ProcError, OSError):
            raw = None
        if raw:
            for off in range(8, len(raw) - 8, 8):
                v = struct.unpack_from("<Q", raw, off)[0]
                if not is_ptr(v):
                    continue
                try:
                    if (self._is_array(v)
                            and self._sane_array_len(v) is not None):
                        return v
                except (ProcError, OSError):
                    continue
        return None

    # --- local-player chain (scan-free) ----------------------------------
    def _group_of(self, local_player: int) -> int | None:
        """The st.Group the local player belongs to, via the player's own
        ``group`` slot. None when unreadable / solo / not attached."""
        if not local_player or not self._is_a(local_player, PLAYER_CLASS):
            return None
        tp = self.hl.ptr(local_player)
        if tp is None:
            return None
        off = self._first_offset(self.hl, tp, _PLAYER_GROUP_FIELDS)
        if not off:
            return None
        group = self.hl.ptr(local_player + off)
        if group and self._is_a(group, GROUP_CLASS):
            return group
        return None

    # --- decode ----------------------------------------------------------
    def decode(self, group_addr: int) -> GroupSnapshot | None:
        """Decode one st.Group instance into a roster.

        None unless the instance class-checks AND its players array unwraps
        to something readable (a wrong pointer can never fabricate a
        member). A genuinely empty players array decodes to a solo snapshot
        - the game's own ``get_isSolo`` is players.length == 0 - so a real
        solo group surfaces instead of reading as "no group"."""
        if not group_addr or not self._is_a(group_addr, GROUP_CLASS):
            return None
        tp = self.hl.ptr(group_addr)
        if tp is None:
            return None
        off = self._first_offset(self.hl, tp, _GROUP_PLAYERS_FIELDS)
        if not off:
            return None
        arr_addr = self._players_array(group_addr + off)
        n = self._sane_array_len(arr_addr) if arr_addr else None
        if n is None:
            return None
        if n == 0:
            return GroupSnapshot(group=group_addr, members=[], solo=True)
        entries = self.hl.array(arr_addr, max_elems=MAX_MEMBERS)
        members: list[GroupMember] = []
        idx1_pos: int | None = None       # where raw array index 1 landed
        for idx, e in enumerate(entries):
            mem = self._decode_member(e)
            if mem is not None:
                if idx == 1:
                    idx1_pos = len(members)
                members.append(mem)
        if not members:
            return None
        snap = GroupSnapshot(group=group_addr, members=members, solo=False)
        if not self._mark_leader(snap, group_addr) and idx1_pos is not None:
            # no leader prop on the object (the live game has none): the
            # bytecode rule is st.Group.get_leader == players[1]
            snap.members[idx1_pos].is_leader = True
        return snap

    def _decode_member(self, player_addr: int) -> GroupMember | None:
        if not player_addr or not self._is_a(player_addr, PLAYER_CLASS):
            return None
        tp = self.hl.ptr(player_addr)
        if tp is None:
            return None
        mem = GroupMember(player=player_addr)
        off = self._first_offset(self.hl, tp, _PLAYER_NAME_FIELDS)
        if off:
            s = self._string_at(player_addr + off)
            if s:
                mem.name = s
        off = self._first_offset(self.hl, tp, _PLAYER_HERO_FIELDS)
        if off:
            h = self.hl.ptr(player_addr + off)
            if h:
                mem.hero = h
        off = self._first_offset(self.hl, tp, _PLAYER_UID_FIELDS)
        if off:
            uid = self._string_at(player_addr + off)
            if uid:
                mem.uid = uid
            else:
                try:                       # numeric uid fallback
                    v = self.hl.i32(player_addr + off)
                    if v:
                        mem.uid = f"{v & 0xFFFFFFFF:08x}"
                except (ProcError, OSError):
                    pass
        off = self._first_offset(self.hl, tp, _PLAYER_ISME_FIELDS)
        if off:
            mem.is_me = self._flag_at(player_addr + off)
        off = self._first_offset(self.hl, tp, _PLAYER_CONN_FIELDS)
        if off:
            mem.connected = self._flag_at(player_addr + off)
        return mem

    def _mark_leader(self, snap: GroupSnapshot, group_addr: int) -> bool:
        """Tag the member the group's ``leader`` prop points at (a build that
        stores the leader may hold its st.Player ptr, hero, uid or name).

        Returns True when a leader was tagged by a stored prop; the caller
        falls back to the bytecode rule (players[1]) when no prop exists -
        the live game declares no leader field at all."""
        tp = self.hl.ptr(group_addr)
        if tp is None:
            return False
        off = self._first_offset(self.hl, tp, _GROUP_LEADER_FIELDS)
        if not off:
            return False
        raw = self.hl.ptr(group_addr + off)
        if raw:
            for m in snap.members:
                if m.player == raw or (m.hero and m.hero == raw):
                    m.is_leader = True
                    return True
        uid = self._string_at(group_addr + off)
        if not uid:
            try:
                v = self.hl.i32(group_addr + off)
                if v:
                    uid = f"{v & 0xFFFFFFFF:08x}"
            except (ProcError, OSError):
                uid = None
        if uid:
            for m in snap.members:
                if m.uid == uid or m.name == uid:
                    m.is_leader = True
                    return True
        return False

    # --- bounded instance scan (fallback) --------------------------------
    def refresh_ranges(self) -> int:
        """Map the st.Group instances, bounded to the small PAGE_READWRITE
        regions HL objects live in (~1s). The full-RW sweep only runs as a
        rare first-map fallback (rate-gated), mirroring meterhud."""
        tp = self.find_type()
        if tp is None:
            return 0
        self._last_remap = time.monotonic()
        insts: set[int] = set()
        small = _small_rw_ranges(self.proc)
        if small:
            try:
                insts = set(self.proc.find_qword_in(tp, small,
                                                    max_hits=4096))
            except (ProcError, OSError):
                insts = set()
        if not insts:
            now = time.monotonic()
            if now - self._last_full_derive >= FULL_DERIVE_S:
                self._last_full_derive = now
                try:
                    insts = set(self.proc.find_qword(tp, rw_only=True,
                                                     max_hits=4096))
                except (ProcError, OSError):
                    insts = set()
        self._anchor_addrs.update(insts)
        if len(self._anchor_addrs) > 8000:
            self._anchor_addrs = set(sorted(self._anchor_addrs)[-8000:])
        try:
            ranges = clip_ranges(cluster_ranges(sorted(self._anchor_addrs)),
                                 self.proc.regions())
        except (ProcError, OSError):
            ranges = []
        self._scan_ranges = ranges
        return len(insts)

    def _instances(self) -> list[int]:
        if not self._scan_ranges or self._group_type is None:
            return []
        try:
            return self.proc.find_qword_in(self._group_type,
                                           self._scan_ranges, max_hits=64)
        except (ProcError, OSError):
            return []

    def _scan_snapshots(self) -> list[GroupSnapshot]:
        """All decodable group instances from the (cluster-bounded) scan."""
        if self._group_type is None and self.find_type() is None:
            return []
        if not self._scan_ranges:
            self.refresh_ranges()
        seen: set[int] = set()
        out: list[GroupSnapshot] = []

        def collect() -> None:
            for g in self._instances():
                s = self.decode(g)
                if s is not None and s.group not in seen:
                    seen.add(s.group)
                    out.append(s)

        collect()
        if not out and time.monotonic() - self._last_remap >= REMAP_EMPTY_S:
            # No live group decoded: the party may have been allocated off
            # the mapped pages. Remap (bounded, ~1s) before giving up.
            self.refresh_ranges()
            collect()
        return out

    @staticmethod
    def _pick(groups: list[GroupSnapshot], local_player: int,
              local_hero: int) -> GroupSnapshot | None:
        """Choose the LOCAL player's group among scanned candidates: one that
        literally contains them wins; else an isMe-flagged roster; else the
        largest (the group you are in is never the smallest)."""
        if not groups:
            return None
        if local_player or local_hero:
            exact = [g for g in groups if g.contains(local_player,
                                                     local_hero)]
            if exact:
                return max(exact, key=lambda g: len(g.members))
        flagged = [g for g in groups
                   if any(mm.is_me for mm in g.members)]
        if flagged:
            return max(flagged, key=lambda g: len(g.members))
        return max(groups, key=lambda g: len(g.members))

    # --- public poll -----------------------------------------------------
    def snapshot(self, local_player: int | None = None,
                 local_hero: int | None = None) -> GroupSnapshot | None:
        """The local player's party roster, rate-limited to ~1 Hz.

        Primary path is scan-free (local ``st.Player.group`` -> the shared
        st.Group). When that chain cannot resolve (drifted build, menu), the
        reader falls back to a bounded heap scan and picks the group that
        contains the local player. None when unattached or when no readable
        group decodes - never a fabricated roster.
        """
        now = time.monotonic()
        if self._snap_at and now - self._snap_at < SNAPSHOT_TTL_S:
            return self._snap
        snap: GroupSnapshot | None = None
        group_addr = self._group_of(local_player or 0)
        if group_addr:
            snap = self.decode(group_addr)
        if snap is None:
            snap = self._pick(self._scan_snapshots(),
                              local_player or 0, local_hero or 0)
        self._snap = snap
        self._snap_at = now
        return snap
