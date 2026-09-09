"""Live hero inspector: buffs (statuses) + equipped gear, read-only.

Walks the offsets already calibrated in constants.py, probe-first:

  buffs: hero + statuses -> hxbit.ArrayProxyData -> array -> st.skill.Status
    kind @ OFF_STATUS_KIND (buff id), stacks @ OFF_STATUS_STACKS (i32),
    duration @ OFF_STATUS_DURATION (f64), originItem @ OFF_STATUS_ITEM
  gear: hero + loadout -> st.Loadout -> equipment -> content (ArrayObj; index
    = gear slot). Each slot is a small per-slot RECORD whose type word has no
    readable class (kind 15), with the slot's item as its last pointer word —
    see _RECORD_SPAN / _slot_item for the live evidence.
    kind @ OFF_GEAR_KIND (gear id), level @ OFF_GEAR_LEVEL (i32),
    rarity + upgradeLevel resolved by name

Every field resolves BY NAME via Hl.field_offset first (survives layout
drift); the fixed constants are fallbacks only. Every pointer is
class-verified before it is trusted, so a wrong slot degrades to an empty
list rather than fabricated rows. Never writes.
"""
from __future__ import annotations

import contextlib
import struct
from functools import lru_cache

from .hl import Hl, is_ptr
from .proc import Proc, ProcError
from ..constants import (
    OFF_HERO_STATUSES, OFF_STATUS_KIND, OFF_STATUS_STACKS,
    OFF_STATUS_DURATION, OFF_STATUS_ITEM,
    OFF_HERO_LOADOUT, OFF_LOADOUT_EQUIPMENT, OFF_EQUIPMENT_CONTENT,
    OFF_GEAR_KIND, OFF_GEAR_LEVEL,
)

_STATUS_ARRAY_FIELDS = ("array", "items", "data")
_HERO_STATUS_FIELDS = ("statuses", "status", "buffs", "statusList",
                       "activeStatuses")
_HERO_LOADOUT_FIELDS = ("loadout", "loadOut", "heroLoadout", "equipmentLoadout")
_LOADOUT_EQUIP_FIELDS = ("equipment", "equip", "gear", "gears")
_EQUIP_CONTENT_FIELDS = ("content", "items", "slots", "gears", "list", "array")
_PROXY_SCAN_BYTES = 0x80
_WALK_SCAN_BYTES = 0x400
_MAX_STATUSES = 64
_MAX_GEAR = 32
# The game's equipment content array is 30 slots, and the index MEANS something:
# itemType.json ends with the game's own `Slot_*` list (Weapon1, Weapon2,
# OffhandWeapon, Head, Neck, Shoulders, Chest, Back, Hands, Waist, Legs, Feet,
# FingerLeft, Trinket, FingerRight, JobTool, Pickaxe, Sickle, Consumable1-4,
# Bag1-6, Glider, Mount = 30, then a Slot_None sentinel). A live read 2026-09-15
# put every filled row of a real character on its game slot (index 2 = offhand
# = where a shield goes), so a caller can name the slots the game reports EMPTY
# instead of showing nothing at all. The table itself lives in the data layer
# (data.items.labels.equip_slot_label) — core must not import that module, which
# reaches ui.theme.
_GEAR_SLOTS = 30

# A content array's elements are not the items themselves. Live-verified
# 2026-09-15 against the running game: each slot is a 0x38-byte per-slot record
# whose type word is a Null-style wrapper (kind 15) with no readable class name
# — so class_of() returns garbage ("(") for every slot, which is why the whole
# walk used to find the chain and then skip all 30 slots. The slot's item is the
# record's LAST pointer word (+0x30, right before the next 0x38-byte record), and
# exactly one st.Item descendant appears inside each record's own span — read on
# all 30 equipment slots and all 40 bag slots. Hence the unwrap below scans the
# record's own span only: a neighbour's item is never in reach, and a slot whose
# record holds no item is skipped rather than guessed at.
_RECORD_SPAN = 0x38

# Live-diag trace: when active (inspect_trace), every walk decision appends a
# human-readable step here. Off by default — zero overhead for the 2 s poll.
_trace: list[str] | None = None


@contextlib.contextmanager
def inspect_trace():
    """Collect the live reader's walk steps (offset hits, class checks, sweep
    results, per-entry verdicts) into a list for the Party page's Diag dump.
    Usage: `with inspect_trace() as tr: rows = hero_gear(proc, hl, addr)` —
    `tr` ends up with one line per decision, in walk order. Nesting is not
    supported (a nested run's steps land in the outer list)."""
    global _trace
    _trace = []
    try:
        yield _trace
    finally:
        _trace = None


def _log(msg: str) -> None:
    if _trace is not None:
        _trace.append(msg)


def _first_off(hl: Hl, type_ptr: int, names: tuple[str, ...],
               fallback: int | None = None) -> int | None:
    for n in names:
        try:
            off = hl.field_offset(type_ptr, n)
        except (ProcError, OSError):
            continue
        if off:
            return off
    return fallback


def _is_array(hl: Hl, addr: int) -> bool:
    try:
        return hl.class_of(addr) in ("hl.types.ArrayDyn", "hl.types.ArrayObj")
    except (ProcError, OSError):
        return False


def _sane_len(hl: Hl, arr: int, cap: int) -> int | None:
    try:
        n = hl.i32(arr + 8)
    except (ProcError, OSError):
        return None
    return n if 0 <= n <= cap else None


def _unwrap_proxy_array(proc: Proc, hl: Hl, proxy: int,
                        cap: int) -> int | None:
    """Real HL array behind an hxbit.ArrayProxyData (or a plain array)."""
    if not is_ptr(proxy):
        return None
    if _is_array(hl, proxy):
        return proxy if _sane_len(hl, proxy, cap) is not None else None
    try:
        if hl.class_of(proxy) != "hxbit.ArrayProxyData":
            return None
    except (ProcError, OSError):
        return None
    tp = hl.ptr(proxy)
    if tp:
        off = _first_off(hl, tp, _STATUS_ARRAY_FIELDS)
        if off:
            a = hl.ptr(proxy + off)
            if a and _is_array(hl, a) and _sane_len(hl, a, cap) is not None:
                return a
            # one more hop: proxy.array may be the Dyn wrapper
            if is_ptr(a):
                try:
                    inner = hl.ptr(a + 0x08)
                except (ProcError, OSError):
                    inner = None
                if is_ptr(inner) and _is_array(hl, inner) \
                        and _sane_len(hl, inner, cap) is not None:
                    return inner
    raw = proc.try_read(proxy, _PROXY_SCAN_BYTES)
    if raw:
        for off in range(8, len(raw) - 7, 8):
            v = struct.unpack_from("<Q", raw, off)[0]
            if not is_ptr(v):
                continue
            if _is_array(hl, v) and _sane_len(hl, v, cap) is not None:
                return v
    return None


def _array_entries(hl: Hl, arr: int, cap: int) -> list[int]:
    """hl.array with an ArrayDyn -> ArrayObj hop handled first."""
    if not is_ptr(arr):
        return []
    try:
        if hl.class_of(arr) == "hl.types.ArrayDyn":
            inner = hl.ptr(arr + 0x08)
            if is_ptr(inner):
                arr = inner
    except (ProcError, OSError):
        pass
    try:
        return hl.array(arr, max_elems=cap)
    except (ProcError, OSError):
        return []


def _sweep_string(hl: Hl, obj: int, max_off: int = 0x100) -> str | None:
    """First String found sweeping an object's head — kind fallback when the
    reflected/fixed kind offset drifted."""
    try:
        blk = hl.proc.try_read(obj, max_off)
    except (ProcError, OSError):
        return None
    if not blk:
        return None
    for off in range(8, len(blk) - 7, 8):
        v = struct.unpack_from("<Q", blk, off)[0]
        if not is_ptr(v):
            continue
        try:
            if hl.class_of(v) == "String":
                s = hl.hl_string(v)
                if s:
                    return s
        except (ProcError, OSError):
            continue
    return None


def _string_field(hl: Hl, obj: int, names: tuple[str, ...],
                  fallback: int | None) -> str | None:
    try:
        tp = hl.ptr(obj)
    except (ProcError, OSError):
        return None
    if tp is None:
        return None
    off = _first_off(hl, tp, names, fallback)
    if not off:
        return None
    try:
        sp = hl.ptr(obj + off)
        if is_ptr(sp) and hl.class_of(sp) == "String":
            return hl.hl_string(sp)
    except (ProcError, OSError):
        pass
    return None


def _sweep_next_object(proc: Proc, hl: Hl, start: int, max_off: int,
                       expect: tuple[str, ...] | None = None) -> int | None:
    """First pointer-sized slot in [start, start+max_off) that reads as a live
    object whose class matches `expect` (a super-chain match when expect has
    several names, e.g. ("st.Equipment",)); None when nothing qualifies."""
    try:
        blk = proc.try_read(start, max_off)
    except (ProcError, OSError):
        return None
    if not blk:
        return None
    for off in range(8, len(blk) - 7, 8):
        v = struct.unpack_from("<Q", blk, off)[0]
        if not is_ptr(v):
            continue
        try:
            cls = hl.class_of(v)
        except (ProcError, OSError):
            continue
        if not cls:
            continue
        if expect:
            try:
                anc = hl.ancestors(hl.ptr(v))
            except (ProcError, OSError):
                continue
            if not any(e in anc for e in expect):
                continue
        return v
    return None


def _is_item(hl: Hl, addr: int) -> bool:
    """True when `addr` is an st.Item or a subclass (st.item.Weapon/Gear/Armor)."""
    if not is_ptr(addr):
        return False
    try:
        cls = hl.class_of(addr)
    except (ProcError, OSError):
        return False
    if not cls:
        return False
    if cls.startswith("st.item."):
        return True
    try:
        return "st.Item" in hl.super_chain(addr)
    except (ProcError, OSError):
        return False


def _type_kind(hl: Hl, obj: int) -> str:
    """The object's hl_type kind as text, or "?" when the type word can't be
    read. Diagnostics only: 15 is the Null-style wrapper the game puts around a
    container's values (the gear slots arrive that way — see _RECORD_SPAN), so
    an unclassed entry reporting kind 15 is the tell that a container wraps its
    values and needs unwrapping."""
    try:
        tp = hl.ptr(obj)
        return str(hl.i32(tp)) if tp is not None else "?"
    except (ProcError, OSError):
        return "?"


def _slot_item(proc: Proc, hl: Hl, entry: int) -> int | None:
    """The item a content-array entry stands for, or None.

    Usually the entry is the per-slot record described at _RECORD_SPAN (its own
    item is the last pointer word of its own span); older containers store the
    item directly, in which case the entry IS the item. Nothing found -> None, so
    an empty or unreadable slot yields no row instead of a fabricated one."""
    if not is_ptr(entry):
        return None
    if _is_item(hl, entry):
        return entry
    try:
        raw = proc.try_read(entry, _RECORD_SPAN)
    except (ProcError, OSError):
        return None
    if not raw:
        return None
    for off in range(8, len(raw) - 7, 8):
        v = struct.unpack_from("<Q", raw, off)[0]
        if is_ptr(v) and _is_item(hl, v):
            return v
    return None


# How MANY of a stackable item a container record holds: the i32 in its own
# span, at the word the live records put the amount in. The record is the same
# kind-15 wrapper described at _RECORD_SPAN, so the field has no name to resolve
# — read live 2026-09-15 across 238 bag and bank entries, where it IS the
# quantity (Soulstone 20, TPDeathStone 123, Cook_11 7, RefillableFlask 3,
# UpgradeAll 672, and exactly 1 for every non-stackable piece).
#
# It is only trusted INSIDE the game's own ceiling for that item type (itemType
# props.stackSize, inherited along the type's chain: 1000 for consumables / food
# / soulstones, 100000 for currency, nothing declared for gear). A word outside
# 1..ceiling — or anything but 1 for a type that declares no stacking — reports
# None, so a build that moves this word shows no count rather than a wrong one.
#
# The bag/bank walk that measured it is gone (a bag's contents are per-character,
# so nothing could read them for anyone but you); the GEAR walk still reports the
# amount for its consumable slots, which is what keeps this on a live path.
_RECORD_COUNT_OFF = 0x28


@lru_cache(maxsize=2048)
def _stack_ceiling(item_id: str) -> int | None:
    """How many of `item_id` fit in one stack, from the game's own itemType
    sheet: the item's type walks its `inherit` chain to the first
    `props.stackSize`. None means the type declares no stacking at all."""
    try:
        from ..data import cdb
        typ = (cdb.by_id("items").get(item_id) or {}).get("type") or ""
        for _ in range(8):
            if not typ:
                break
            row = cdb.by_id("itemType").get(typ) or {}
            size = (row.get("props") or {}).get("stackSize")
            if isinstance(size, (int, float)):
                return int(size)
            typ = row.get("inherit") or ""
    except Exception:                     # no data layer: no ceiling, no count
        return None
    return None


def _record_count(proc: Proc, hl: Hl, record: int, item_id: str) -> int | None:
    """How many of `item_id` this container record holds, or None — see
    _RECORD_COUNT_OFF for the live evidence and the ceiling guard."""
    if not record:
        return None
    try:
        n = hl.i32(record + _RECORD_COUNT_OFF)
    except (ProcError, OSError):
        return None
    ceiling = _stack_ceiling(item_id)
    if ceiling is None:
        return n if n == 1 else None
    return n if 1 <= n <= ceiling else None


def _hero_loadout(proc: Proc, hl: Hl, hero_addr: int, tag: str) -> int | None:
    """hero -> st.Loadout: field name first, then the fixed constant, then a
    class-verified pointer sweep of the object head (OFF_HERO_LOADOUT has
    drifted before). Shared by the gear and pet walks so both degrade the
    same way — no loadout means no rows, never a fabricated one."""
    try:
        htp = hl.ptr(hero_addr)
        if htp is None:
            _log(f"{tag}: hero @{hero_addr:#x} has no type ptr")
            return None
        lo_off = _first_off(hl, htp, ("loadout", "loadOut", "heroLoadout"),
                            OFF_HERO_LOADOUT)
        if not lo_off:
            _log(f"{tag}: hero @{hero_addr:#x} type @{htp:#x} — no loadout "
                 f"offset")
        else:
            p = hl.ptr(hero_addr + lo_off)
            if is_ptr(p) and (hl.class_of(p) or "").startswith("st.Loadout"):
                _log(f"{tag}: loadout offset {lo_off:#x} -> @{p:#x} "
                     f"({hl.class_of(p)})")
                return p
        loadout = _sweep_next_object(proc, hl, hero_addr, _WALK_SCAN_BYTES,
                                     ("st.Loadout",))
        _log(f"{tag}: loadout sweep -> @{loadout:#x}" if loadout else
             f"{tag}: loadout sweep miss")
        return loadout
    except (ProcError, OSError) as e:
        _log(f"{tag}: loadout walk aborted: {e!r}")
        return None


def _read_item(hl: Hl, item: int) -> dict:
    """The fields one st.Item exposes, every one BY NAME (no guessed offset):
    {cls, kind, level, rarity, upgrade} plus the offsets used so a caller can
    trace them. An unresolvable field stays None rather than inventing a
    value."""
    out = {"cls": None, "kind": None, "level": None, "rarity": None,
           "upgrade": None, "kind_off": None, "level_off": None,
           "upgrade_off": None}
    try:
        out["cls"] = hl.class_of(item) or "?"
        gtp = hl.ptr(item)
    except (ProcError, OSError):
        return out
    k_off = _first_off(hl, gtp, ("kind", "id", "gearId"),
                       OFF_GEAR_KIND) if gtp else OFF_GEAR_KIND
    l_off = _first_off(hl, gtp, ("level", "lvl", "itemLevel"),
                       OFF_GEAR_LEVEL) if gtp else OFF_GEAR_LEVEL
    # rarity / upgrade rank have no calibrated constant: they resolve by name
    # off the piece itself or stay None (never a guessed offset)
    u_off = _first_off(hl, gtp, ("upgradeLevel", "upgrade", "plus"),
                       None) if gtp else None
    out["kind_off"], out["level_off"], out["upgrade_off"] = k_off, l_off, u_off
    if k_off:
        try:
            kp = hl.ptr(item + k_off)
            if is_ptr(kp) and hl.class_of(kp) == "String":
                out["kind"] = hl.hl_string(kp)
        except (ProcError, OSError):
            pass
    if l_off:
        try:
            lvl = hl.i32(item + l_off)
            out["level"] = lvl if 0 <= lvl <= 999 else None
        except (ProcError, OSError):
            pass
    try:
        out["rarity"] = _string_field(hl, item, ("rarity",), None)
    except (ProcError, OSError):
        pass
    if not out["rarity"] and out["kind"]:
        # Some live item objects (e.g. armor) carry no rarity string —
        # fall back to the sheet's authored rarity for the item id (`kind`,
        # not `cls`: that is the HashLink record class, not the id).
        out["rarity"] = _sheet_rarity(out["kind"])
    if u_off:
        try:
            up = hl.i32(item + u_off)
            out["upgrade"] = up if 0 <= up <= 99 else None
        except (ProcError, OSError):
            pass
    return out


@lru_cache(maxsize=None)
def _sheet_rarity(item_id: str) -> str | None:
    """Authored rarity for `item_id` from the static sheet (read-only)."""
    with contextlib.suppress(Exception):
        from ..data.items.sources import item_display_rarity
        r = item_display_rarity(item_id)
        return r or None
    return None


# --- the equipped companion (hero -> loadout -> pet id) ---------------------
# The pet is NOT a slot: the game's own slot table has no Slot_Pet (the nearest
# is Slot_Mount at 29), it is a String on st.Loadout — live 2026-09-15 at +0xc0
# holding 'Goat_Demon'. That id is what both the unit sheet and the scene entity
# key on, which is what lets the page name the creature from the game's own data
# and join it to the creature actually loaded in the world.
#
# Resolved BY NAME only, with no calibrated constant: a build that renames or
# moves the field reports no pet rather than reading a guessed pointer.
_PET_FIELDS = ("pet", "petId", "petUnit", "companion")


def hero_pet(proc: Proc, hl: Hl, hero_addr: int) -> dict:
    """The hero's equipped companion: {"id", "off", "cls"}.

    `id` is the game's own unit id ('Goat_Demon'). `off` is the st.Loadout field
    it came from, which is what separates the two nothings: `off` None means this
    build exposes no pet field at all (a reader limit), while an `off` with `id`
    None means the loadout says no pet is equipped — the game's own answer, never
    a fabricated one. `cls` is the class the field held, for the Diag dump when
    that turns out not to be a String.
    """
    out = {"id": None, "off": None, "cls": None}
    if not hero_addr:
        _log("pet: no hero address")
        return out
    loadout = _hero_loadout(proc, hl, hero_addr, "pet")
    if loadout is None:
        return out
    try:
        ltp = hl.ptr(loadout)
        off = _first_off(hl, ltp, _PET_FIELDS, None) if ltp else None
        if not off:
            _log(f"pet: st.Loadout @{loadout:#x} — no pet field "
                 f"({', '.join(_PET_FIELDS)})")
            return out
        out["off"] = off
        p = hl.ptr(loadout + off)
    except (ProcError, OSError) as e:
        _log(f"pet: walk aborted: {e!r}")
        return out
    if not is_ptr(p):
        _log(f"pet: field {off:#x} is null — no pet equipped")
        return out
    try:
        cls = hl.class_of(p)
    except (ProcError, OSError):
        cls = None
    out["cls"] = cls
    if cls != "String":
        _log(f"pet: field {off:#x} -> @{p:#x} is {cls or '?'}, not a String")
        return out
    try:
        out["id"] = hl.hl_string(p)
    except (ProcError, OSError):
        pass
    _log(f"pet: loadout pet {off:#x} -> {out['id']!r}")
    return out


def hero_buffs(proc: Proc, hl: Hl, hero_addr: int) -> list[dict]:
    """Buffs on one hero: [{kind, stacks, duration, source_item}].

    Traced like the gear walk (see inspect_trace), because this is the column a
    container change would quietly empty: the per-entry verdict reports the
    entry's TYPE WORD KIND, so a Null-style (kind 15) status cell shows up in
    the Party page's Diag dump instead of reading as "no buffs live"."""
    out: list[dict] = []
    if not hero_addr:
        _log("buff: no hero address")
        return out
    try:
        htp = hl.ptr(hero_addr)
        if htp is None:
            _log(f"buff: hero @{hero_addr:#x} has no type ptr")
            return out
        off = _first_off(hl, htp, ("statuses", "status", "buffs"),
                         OFF_HERO_STATUSES)
        if not off:
            _log(f"buff: hero type @{htp:#x} — no statuses offset")
            return out
        proxy = hl.ptr(hero_addr + off)
        _log(f"buff: hero @{hero_addr:#x} statuses offset {off:#x} -> "
             f"{proxy if proxy is None else hex(proxy)} "
             f"({hl.class_of(proxy) if is_ptr(proxy) else 'null'})")
        arr = _unwrap_proxy_array(proc, hl, proxy or 0, _MAX_STATUSES)
        if arr is None:
            _log("buff: no live array behind the statuses prop")
            return out
        entries = hl.array(arr, max_elems=_MAX_STATUSES)
        _log(f"buff: array @ {arr:#x} ({hl.class_of(arr)}) — "
             f"{len(entries)} entries")
    except (ProcError, OSError) as e:
        _log(f"buff: walk aborted: {e!r}")
        return out
    for i, e in enumerate(entries):
        if not is_ptr(e):
            _log(f"buff: entry {i} — null")
            continue
        try:
            cls = hl.class_of(e)
        except (ProcError, OSError):
            _log(f"buff: entry {i} @{e:#x} — unreadable")
            continue
        if not cls or "Status" not in cls:
            _log(f"buff: entry {i} @{e:#x} — skip (class {cls or '?'}; "
                 f"type kind {_type_kind(hl, e)}; empty slot or not a status)")
            continue
        try:
            etp = hl.ptr(e)
            k_off = _first_off(hl, etp, ("kind", "id", "statusId"),
                               OFF_STATUS_KIND) if etp else OFF_STATUS_KIND
            s_off = _first_off(hl, etp, ("stacks", "stack"),
                               OFF_STATUS_STACKS) if etp else OFF_STATUS_STACKS
            d_off = _first_off(hl, etp, ("duration", "timeLeft", "remaining"),
                               OFF_STATUS_DURATION) if etp else OFF_STATUS_DURATION
            i_off = _first_off(hl, etp, ("originItem", "item", "sourceItem"),
                               OFF_STATUS_ITEM) if etp else OFF_STATUS_ITEM
            kind = None
            if k_off:
                kp = hl.ptr(e + k_off)
                if is_ptr(kp) and hl.class_of(kp) == "String":
                    kind = hl.hl_string(kp)
            stacks = None
            if s_off:
                try:
                    stacks = hl.i32(e + s_off)
                    if not (0 <= stacks <= 9999):
                        stacks = None
                except (ProcError, OSError):
                    stacks = None
            dur = None
            if d_off:
                try:
                    dur = hl.f64(e + d_off)
                    if not (dur is not None and 0.0 <= dur < 1e9):
                        dur = None
                except (ProcError, OSError):
                    dur = None
            src = None
            if i_off:
                ip = hl.ptr(e + i_off)
                if is_ptr(ip):
                    src = _string_field(hl, ip, ("kind", "id", "itemId"), None)
            out.append({"kind": kind or "?", "stacks": stacks,
                        "duration": dur, "source_item": src})
        except (ProcError, OSError):
            continue
    _log(f"buff: {len(out)} rows from {len(entries)} entries")
    return out


def _empty_slot(slot: int, state: str, record: int,
                cls: str | None = None) -> dict:
    """A row for a slot with no item. `state` says WHICH kind of nothing:
    "empty" when the game left the slot null, "unreadable" when a live entry was
    there but its item could not be unwrapped. The two are not the same thing —
    an "empty" offhand means no shield is equipped, an "unreadable" one means
    this reader could not get it — and only the difference tells a reader which
    one they are looking at."""
    return {"slot": slot, "kind": None, "level": None, "rarity": None,
            "upgrade": None, "cls": cls, "addr": 0, "record": record,
            "state": state}


def hero_gear(proc: Proc, hl: Hl, hero_addr: int,
              include_empty: bool = False) -> list[dict]:
    """Equipped items on one hero (the local player included): one row per
    filled slot, [{slot, kind, level, rarity, upgrade, cls, addr, record,
    state}].

    `state` is "filled"; with `include_empty=True` every other slot of the
    game's 30-slot array comes back too, as {slot, state} rows that are either
    "empty" (the game holds null there) or "unreadable" (a live entry whose item
    this reader cannot unwrap) — see _empty_slot for why the difference matters.
    The index is the game's OWN order (see _GEAR_SLOTS): 2 is the offhand, so a
    caller can name a slot the game reports as empty rather than omitting it.

    Walk hero -> loadout -> equipment -> content array. Every hop resolves by
    field name first, then the fixed constant, then a class-verified pointer
    sweep of the object head — a Null<st.Equipment> (empty slots), a proxy/Dyn
    content array or a drifted offset each degrade to an empty list, never a
    fabricated row. Each slot's own item comes from _slot_item, which unwraps the
    live per-slot record (see _RECORD_SPAN): `addr` is the item and `record` the
    slot wrapper the game holds it in."""
    out: list[dict] = []
    if not hero_addr:
        _log("gear: no hero address")
        return out
    try:
        # 1. loadout (shared with the bag walk — see _hero_loadout)
        loadout = _hero_loadout(proc, hl, hero_addr, "gear")
        if loadout is None:
            return out
        # 2. equipment (field name -> fixed -> sweep). Empty equipment slots
        #    store a Null here, so a null read is a normal outcome, not an error.
        ltp = hl.ptr(loadout)
        eq_off = _first_off(hl, ltp, ("equipment", "equip"),
                            OFF_LOADOUT_EQUIPMENT) if ltp else \
            OFF_LOADOUT_EQUIPMENT
        _log(f"gear: loadout type @{ltp:#x} equipment offset {eq_off:#x}"
             if eq_off else "gear: loadout — no equipment offset")
        equip = None
        if eq_off:
            p = hl.ptr(loadout + eq_off)
            if is_ptr(p):
                try:
                    if "st.Equipment" in hl.ancestors(hl.ptr(p)):
                        equip = p
                except (ProcError, OSError):
                    equip = None
        if equip is None:
            equip = _sweep_next_object(proc, hl, loadout, _WALK_SCAN_BYTES,
                                       ("st.Equipment",))
            _log(f"gear: equipment via sweep -> "
                 f"@{equip:#x}" if equip else "gear: equipment sweep miss")
        else:
            _log(f"gear: equipment @ {equip:#x} ({hl.class_of(equip)})")
        if equip is None:
            return out
        # 3. content array (field name -> fixed -> proxy unwrap). The array
        #    may be a plain ArrayObj, an ArrayDyn wrapper or an hxbit proxy.
        etp = hl.ptr(equip)
        c_off = _first_off(hl, etp, ("content", "items", "slots", "gears"),
                           OFF_EQUIPMENT_CONTENT) if etp else \
            OFF_EQUIPMENT_CONTENT
        _log(f"gear: equipment type @{etp:#x} content offset {c_off:#x}"
             if c_off else "gear: equipment — no content offset")
        arr = None
        if c_off:
            p = hl.ptr(equip + c_off)
            arr = _unwrap_proxy_array(proc, hl, p or 0, _MAX_GEAR)
        if arr is None:
            # sweep the equipment head for ANY live array; entries are
            # class-verified below, so a wrong slot yields zero rows
            _log("gear: content offset miss — sweeping equipment head")
            try:
                blk = proc.try_read(equip, _WALK_SCAN_BYTES)
            except (ProcError, OSError):
                blk = None
            if blk:
                for off in range(8, len(blk) - 7, 8):
                    v = struct.unpack_from("<Q", blk, off)[0]
                    if not is_ptr(v):
                        continue
                    a = _unwrap_proxy_array(proc, hl, v, _MAX_GEAR)
                    if a is not None:
                        arr = a
                        _log(f"gear: array sweep hit @ +{off:#x}")
                        break
        if arr is None:
            _log("gear: no content array found")
            return out
        _log(f"gear: content array @ {arr:#x} ({hl.class_of(arr)})")
        entries = hl.array(arr, max_elems=_MAX_GEAR)
        _log(f"gear: {len(entries)} array entries")
    except (ProcError, OSError) as e:
        _log(f"gear: walk aborted: {e!r}")
        return out
    for slot, entry in enumerate(entries):
        if not is_ptr(entry):
            _log(f"gear: slot {slot} — null entry")
            if include_empty:
                out.append(_empty_slot(slot, "empty", 0))
            continue
        item = _slot_item(proc, hl, entry)
        if item is None:
            try:
                cls = hl.class_of(entry)
            except (ProcError, OSError):
                cls = None
            _log(f"gear: slot {slot} — skip (entry @{entry:#x} class "
                 f"{cls or '?'} holds no item; empty slot)")
            if include_empty:
                out.append(_empty_slot(slot, "unreadable", entry, cls))
            continue
        if item != entry:
            _log(f"gear: slot {slot} — record @{entry:#x} -> item @{item:#x}")
        try:
            # every field off the piece resolves by name — see _read_item
            f = _read_item(hl, item)
            ko = f"{f['kind_off']:#x}" if f["kind_off"] else "?"
            lo = f"{f['level_off']:#x}" if f["level_off"] else "?"
            tail = ""
            if f["rarity"] is not None:
                tail += f" rarity={f['rarity']!r}"
            if f["upgrade"] is not None:
                tail += (f" upgrade=+{f['upgrade']} "
                         f"(off {f['upgrade_off']:#x})")
            _log(f"gear: slot {slot} {f['cls']} kind={f['kind']!r} (off {ko}) "
                 f"level={f['level']!r} (off {lo}){tail}")
            out.append({"slot": slot, "kind": f["kind"] or "?",
                        "level": f["level"], "rarity": f["rarity"],
                        "upgrade": f["upgrade"], "cls": f["cls"],
                        "state": "filled",
                        # an EQUIPMENT record states 1 for gear and 0 for a
                        # stackable (its amount lives on the bag stack), so this
                        # is usually 1 here and None for a consumable slot
                        "count": _record_count(proc, hl, entry, f["kind"] or ""),
                        # `addr` is the ITEM (callers read more fields off the
                        # same piece here); `record` is the slot wrapper the game
                        # holds it in - both, so neither has to be re-walked
                        "addr": item, "record": entry})
        except (ProcError, OSError):
            continue
    if include_empty:
        # pad the tail of the game's array: a build that hands back fewer than
        # 30 entries still gets one row per slot, so a caller can name every
        # slot rather than inferring which ones the array omitted
        have = {r["slot"] for r in out}
        for slot in range(len(entries), _GEAR_SLOTS):
            if slot not in have:
                out.append(_empty_slot(slot, "empty", 0))
    return out
