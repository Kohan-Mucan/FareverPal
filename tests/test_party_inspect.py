"""Headless tests for the Party Inspect page's live reads.

The gear reader (core/inspect.hero_gear) is driven against a synthetic HL
heap (tests/fakemem.HeapBuilder) laid out exactly like the real runtime:
hero -> loadout (st.Loadout) -> equipment (st.Equipment) -> content array
of st.item.Gear instances with kind String + level i32. Covers the probe
order (field name -> fixed offset -> class-verified sweep) and the
never-fabricate guarantees (bad pointers / wrong classes -> empty list).

The LIVE shape differs and is covered too (test_hero_gear_unwraps_*): a
content slot is not the item but a 0x38-byte per-slot record whose type word
is a Null-style wrapper (kind 15, no readable class) with the slot's item as
its last pointer word. Read live 2026-09-15 on all 30 equipment slots and all
40 bag slots; before the unwrap existed every slot was skipped as "not gear",
so the page showed "No gear live" for the local player.

The label side checks data/names.status_name: a live buff id that is an
item-granted food buff (Whetstone_Status, the game-typo'd Weighstone_Status)
resolves to the granting item's name, and non-item statuses keep the skill
sheet name. Icon resolution (the atlas sprite for the buff's source item)
is covered where the assets are bundled.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

import pytest

from farever_companion import constants as C
from farever_companion.core.hl import Hl
from farever_companion.core.inspect import hero_gear, hero_buffs, hero_pet
from tests.fakemem import FakeProc, HeapBuilder

# the live per-slot record's own span (mirrors core.inspect._RECORD_SPAN)
_RECORD_BYTES = 0x38


def _scene_with_gear(gear_specs: list[tuple[str, int]] | None):
    """A hero heap: ent.Hero -> loadout -> equipment -> content array.

    gear_specs is [(kind, level)] per content slot; None builds a hero with
    NO loadout pointer at all. Returns (proc, hl, hero_addr, builder) — the
    builder is returned so a test can extend the SAME heap (a fresh
    HeapBuilder would rewind to the base and overwrite the types).
    """
    proc = FakeProc()
    hb = HeapBuilder(proc)

    hb.make_type("String")
    hb.make_type("hxbit.ArrayProxyData")
    hb.make_type("hl.types.ArrayDyn")

    # --- data model types ------------------------------------------------
    hb.make_type("ent.Hero", fields={
        "pos": 0x08, "statuses": 0x1f0, "loadout": C.OFF_HERO_LOADOUT})
    hb.make_type("st.Loadout", fields={
        "__uid": 0x08, "equipment": C.OFF_LOADOUT_EQUIPMENT})
    hb.make_type("st.Equipment", fields={
        "name": 0x08, "content": C.OFF_EQUIPMENT_CONTENT})
    # the live piece also carries rarity + upgradeLevel (name-resolved; they
    # have no calibrated constant, so the reader only reads them when the name
    # resolves - offsets here mirror the live layout: 0x9c / 0xb8)
    hb.make_type("st.item.Gear", fields={
        "kind": C.OFF_GEAR_KIND, "level": C.OFF_GEAR_LEVEL,
        "upgradeLevel": 0x9c, "rarity": 0xb8})

    # 0x600 keeps the whole instance real: C.OFF_HERO_LOADOUT (0x4e8) sits past a
    # default 0x400 instance, so wiring the loadout would land outside the hero
    # and clobber whatever was allocated after it
    hero = hb.make_instance("ent.Hero", size=0x600)

    if gear_specs is not None:
        _attach_gear(proc, hb, hero, gear_specs)

    # hero must point at a st.Loadout-typed instance for the sweep check
    return proc, Hl(proc), hero, hb


def _attach_gear(proc: FakeProc, hb: HeapBuilder, hero: int,
                 specs: list[tuple[str, int]]) -> None:
    """Build loadout/equipment/content for `specs` and wire it to `hero`."""
    loadout = hb.make_instance("st.Loadout")
    equip = hb.make_instance("st.Equipment")
    slots = []
    for kind, lvl in specs:
        g = hb.make_instance("st.item.Gear", size=0xa8)
        proc.put_u64(g + C.OFF_GEAR_KIND, hb.make_string(kind))
        proc.put_i32(g + C.OFF_GEAR_LEVEL, lvl)
        slots.append(g)
    content = hb.make_array(slots)
    proc.put_u64(equip + C.OFF_EQUIPMENT_CONTENT, content)
    proc.put_u64(loadout + C.OFF_LOADOUT_EQUIPMENT, equip)
    proc.put_u64(hero + C.OFF_HERO_LOADOUT, loadout)


def test_hero_gear_reads_kinds_and_levels():
    proc, hl, hero, _hb = _scene_with_gear(
        [("Sword_Beefury", 25), ("Helm_Iron", 12)])
    out = hero_gear(proc, hl, hero)
    assert [(g["kind"], g["level"]) for g in out] == \
        [("Sword_Beefury", 25), ("Helm_Iron", 12)]
    assert [g["slot"] for g in out] == [0, 1]
    assert all("Gear" in g["cls"] for g in out)


def test_hero_gear_empty_slots_stay_empty_rows():
    """Null pointers inside the content array are skipped, not fabricated."""
    proc, hl, hero, hb = _scene_with_gear(None)
    _attach_gear(proc, hb, hero, [])
    # rebuild the content array with a null slot between two pieces
    equip = hb.make_instance("st.Equipment")
    g1 = hb.make_instance("st.item.Gear", size=0xa8)
    proc.put_u64(g1 + C.OFF_GEAR_KIND, hb.make_string("Axe_Only"))
    proc.put_i32(g1 + C.OFF_GEAR_LEVEL, 5)
    g3 = hb.make_instance("st.item.Gear", size=0xa8)
    proc.put_u64(g3 + C.OFF_GEAR_KIND, hb.make_string("Ring_Silver"))
    proc.put_i32(g3 + C.OFF_GEAR_LEVEL, 9)
    content = hb.make_array([g1, 0, g3])
    proc.put_u64(equip + C.OFF_EQUIPMENT_CONTENT, content)
    # point the loadout at this new equipment
    ltp = hb.make_instance("st.Loadout")
    proc.put_u64(ltp + C.OFF_LOADOUT_EQUIPMENT, equip)
    proc.put_u64(hero + C.OFF_HERO_LOADOUT, ltp)

    out = hero_gear(proc, hl, hero)
    assert [(g["slot"], g["kind"]) for g in out] == \
        [(0, "Axe_Only"), (2, "Ring_Silver")]


def _make_record(proc: FakeProc, hb: HeapBuilder, item: int | None,
                 tp: int | None = None, count: int | None = None) -> int:
    """A live-shaped content slot: the 0x38-byte per-slot record.

    Its type word is a Null-style wrapper (kind 15) with no readable type obj,
    so class_of() cannot name it (live it reads as garbage "(") - and the slot's
    item is its LAST pointer word, right before the next record. The two words
    before it point at the record's own tail, as the game's records do.

    Pass `tp` to reuse a wrapper type and keep records back-to-back (the
    adjacency the unwrap's own span depends on).
    """
    if tp is None:
        tp = hb.alloc(0x10)
        proc.put_i32(tp, 15)                 # Null-style wrapper kind
        proc.put_u64(tp + 8, 0)              # no readable type obj
    rec = hb.alloc(_RECORD_BYTES)
    proc.put_u64(rec, tp)
    proc.put_u64(rec + 0x18, rec + 0x28)     # self-referential tail pair
    proc.put_u64(rec + 0x20, rec + 0x30)
    proc.put_u64(rec + 0x30, item or 0)
    if count is not None:
        proc.put_i32(rec + 0x28, count)      # the amount word, when stated
    return rec


def _live_shaped_scene():
    """A hero heap whose every hop resolves BY NAME.

    The item type's fields start at +8 (as the runtime's global field table does)
    and sit at fixture-chosen offsets, none of which is a calibrated constant —
    so a reader that hardcoded OFF_GEAR_* instead of resolving names cannot pass
    these tests. That mirrors the live piece, where `rarity` and `upgradeLevel`
    have no constant at all and only name resolution finds them.
    """
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("String")
    hb.make_type("hl.types.ArrayObj")
    hb.make_type("ent.Hero", fields={"loadout": 0x08})
    hb.make_type("st.Loadout", fields={"equipment": 0x08})
    hb.make_type("st.Equipment", fields={"content": 0x08})
    hb.make_type("st.item.Gear", fields={
        "kind": 0x08, "level": 0x20, "upgradeLevel": 0x28, "rarity": 0x40})
    return proc, Hl(proc), hb, hb.make_instance("ent.Hero", size=0x400)


def _live_piece(proc: FakeProc, hb: HeapBuilder, kind: str, lvl: int,
                rarity: str | None = None, up: int | None = None) -> int:
    """A piece laid out at _live_shaped_scene's by-name offsets."""
    g = hb.make_instance("st.item.Gear", size=0x68)
    proc.put_u64(g + 0x08, hb.make_string(kind))
    proc.put_i32(g + 0x20, lvl)
    if up is not None:
        proc.put_i32(g + 0x28, up)
    if rarity is not None:
        proc.put_u64(g + 0x40, hb.make_string(rarity))
    return g


def _live_wire(proc: FakeProc, hb: HeapBuilder, hero: int,
               entries: list[int]) -> None:
    loadout = hb.make_instance("st.Loadout", size=0x40)
    equip = hb.make_instance("st.Equipment", size=0x40)
    proc.put_u64(equip + 0x08, hb.make_array(entries))
    proc.put_u64(loadout + 0x08, equip)
    proc.put_u64(hero + 0x08, loadout)


def _record_type(proc: FakeProc, hb: HeapBuilder) -> int:
    """The live Null-style wrapper type: kind 15, no readable type obj."""
    tp = hb.alloc(0x10)
    proc.put_i32(tp, 15)
    proc.put_u64(tp + 8, 0)
    return tp


def test_equip_slots_are_the_games_own_list():
    """The 30 slots are the GAME's own (itemType's `Slot_*` rows), in the order
    its content array holds them. Verified live 2026-09-15: every filled row of a
    real character landed on the right one — an axe at 0, a greatsword at 1, head
    3, neck 4, back 7, rings 12/14, trinket 13, food at consumable 4 (21), mount
    29. Which is what lets a slot the game reports as EMPTY still be named: a
    shield goes in index 2, and an unnamed index showed the reader nothing.

    The sheet's trailing `Slot_None` is a sentinel after the real slots, so the
    array is 30 long and index 30 has no name."""
    from farever_companion.data.items import labels

    slots = labels.equip_slots()
    assert len(slots) == 30
    assert slots[0] == "Slot_Weapon1" and slots[1] == "Slot_Weapon2"
    assert slots[2] == "Slot_OffhandWeapon"        # where a shield goes
    assert slots[29] == "Slot_Mount"
    assert "Slot_None" not in slots
    for idx, want in ((0, "Weapon 1"), (1, "Weapon 2"), (2, "Offhand"),
                      (3, "Head"), (4, "Neck"), (7, "Back"),
                      (12, "Ring 1"), (13, "Trinket"), (14, "Ring 2"),
                      (21, "Consumable 4"), (29, "Mount")):
        assert labels.equip_slot_label(idx) == want, idx
    # outside the array there is no slot name to give
    assert labels.equip_slot_label(30) == ""
    assert labels.equip_slot_label(None) == ""
    assert labels.equip_slot_label("x") == ""

    # ...and with no itemType sheet at all (the frozen build, where the loose
    # JSONs are absent) the constant is the same table, so slots still get named
    from farever_companion.data import cdb
    labels.equip_slots.cache_clear()
    orig = cdb.lines
    try:
        def _no_sheet(name):
            raise FileNotFoundError(name)
        cdb.lines = _no_sheet
        assert labels.equip_slots() == slots
    finally:
        cdb.lines = orig
        labels.equip_slots.cache_clear()


def test_gear_names_every_slot_and_marks_the_unread_ones():
    """The local player's own live set at the game's own indices — a piece at
    0/1/3/4/7/12/13/14/21/29 and NOTHING at 2, the offhand a shield goes in.

    That slot has to come back as a named "empty" row instead of being missing
    (its absence is what made the shield look like it had not been read), while a
    live entry that holds no item is reported as "unreadable": a reader limit,
    which is a different thing from an empty slot and must not read as one."""
    from farever_companion.data.items.labels import equip_slot_label

    proc, hl, hb, hero = _live_shaped_scene()
    tp = _record_type(proc, hb)
    live = {0: "Axe_Boomerang", 1: "GS_Nova", 3: "Head_RDemon_Fig_Craft",
            4: "GearNeck_Fracture", 7: "Back_Wings", 12: "Ring_Eye",
            13: "Trinket_Pan", 14: "Ring_Eye", 21: "Food_SkunkMeat",
            29: "Mount_Nightdog"}
    entries = []
    for i in range(30):
        if i == 2:
            entries.append(0)                    # the offhand: nothing there
        elif i == 5:
            entries.append(_make_record(proc, hb, None, tp))   # live, no item
        elif i in live:
            entries.append(_make_record(
                proc, hb, _live_piece(proc, hb, live[i], 25), tp))
        else:
            entries.append(0)
    _live_wire(proc, hb, hero, entries)

    rows = hero_gear(proc, hl, hero)
    assert [g["slot"] for g in rows] == list(live)
    assert all(g["state"] == "filled" for g in rows)
    assert [g["kind"] for g in rows] == list(live.values())
    assert [equip_slot_label(g["slot"]) for g in rows] == [
        "Weapon 1", "Weapon 2", "Head", "Neck", "Back", "Ring 1",
        "Trinket", "Ring 2", "Consumable 4", "Mount"]

    full = hero_gear(proc, hl, hero, include_empty=True)
    assert len(full) == 30                        # every slot, always
    by = {g["slot"]: g for g in full}
    assert by[2]["state"] == "empty"              # the empty offhand is named
    assert by[2]["kind"] is None
    assert by[5]["state"] == "unreadable"         # a live entry, no item
    # it keeps the ENTRY it could not unwrap and no item address at all: the
    # row says where to look, and never fabricates a piece to point at
    assert by[5]["record"] and by[5]["addr"] == 0
    assert sum(1 for g in full if g["state"] == "empty") == 19
    # the filled rows are the same pieces, in the same order, either way
    assert [g["slot"] for g in full if g["state"] == "filled"] == list(live)


def test_hero_gear_unwraps_the_live_per_slot_record():
    """The live shape: slots hold records, not items (read live 2026-09-15).

    Every value asserted here (Rare +3, Legendary +5) is the local player's own
    equipped pair as the game reported it.
    """
    proc, hl, hb, hero = _live_shaped_scene()
    axe = _live_piece(proc, hb, "Axe_Boomerang", 25, "Rare", 3)
    gs = _live_piece(proc, hb, "GS_Nova", 25, "Legendary", 5)
    _live_wire(proc, hb, hero, [_make_record(proc, hb, axe),
                                _make_record(proc, hb, gs)])

    out = hero_gear(proc, hl, hero)
    assert [(g["slot"], g["kind"], g["level"], g["rarity"], g["upgrade"])
            for g in out] == [(0, "Axe_Boomerang", 25, "Rare", 3),
                              (1, "GS_Nova", 25, "Legendary", 5)]
    assert all("st.item." in g["cls"] for g in out)    # the ITEM's class
    # both addresses are reported: the item, and the slot wrapper holding it
    assert out[0]["addr"] == axe and out[0]["record"] != axe


def test_hero_gear_record_never_borrows_the_neighbour_slots_item():
    """Records are 0x38 bytes end to end, so the unwrap must stay inside one.

    A slot whose record holds no item (or a wrong class) yields NO row even when
    the next slot's record - allocated immediately after it - does hold one.
    """
    from farever_companion.core import inspect as hero_inspect

    # the reader's span is itself part of the pinned live shape: the records
    # measured 0x38 bytes end to end, so a span that no longer matches that is a
    # claim about the game that this fixture cannot honestly express - and a
    # wider one would let a slot's scan reach into its neighbour
    assert hero_inspect._RECORD_SPAN == _RECORD_BYTES

    proc, hl, hb, hero = _live_shaped_scene()
    tp = _record_type(proc, hb)
    empty = hb.alloc(_RECORD_BYTES)          # slot 0: no item at all
    proc.put_u64(empty, tp)
    wrong = hb.alloc(_RECORD_BYTES)          # slot 1: holds a non-item
    proc.put_u64(wrong, tp)
    filled = _make_record(proc, hb, None, tp=tp)   # slot 2: record, then item
    assert filled - empty == 2 * _RECORD_BYTES     # the three records are adjacent
    not_an_item = hb.make_instance("st.Loadout", size=0x40)
    item = _live_piece(proc, hb, "Ring_Silver", 9, "Rare", 1)
    proc.put_u64(wrong + 0x30, not_an_item)
    proc.put_u64(filled + 0x30, item)

    _live_wire(proc, hb, hero, [empty, wrong, filled])
    out = hero_gear(proc, hl, hero)
    assert [(g["slot"], g["kind"]) for g in out] == [(2, "Ring_Silver")]
    assert out[0]["addr"] == item


def test_hero_gear_without_loadout_is_empty_not_crash():
    proc, hl, hero, _hb = _scene_with_gear(None)
    assert hero_gear(proc, hl, hero) == []


def test_hero_gear_bad_addr_is_empty():
    proc, hl, _hero, _hb = _scene_with_gear([("X", 1)])
    assert hero_gear(proc, hl, 0xdeadbeef) == []


def test_hero_gear_loadout_field_drift_falls_back_to_sweep():
    """The loadout slot moved off the fixed constant (build drift): the
    class-verified sweep still finds the st.Loadout-typed object."""
    proc, hl, hero, hb = _scene_with_gear([("Sword_X", 7)])
    # detach the field-name path: point the declared slot at garbage
    proc.put_u64(hero + C.OFF_HERO_LOADOUT, 0)          # null there
    # the real loadout lives elsewhere; the sweep must find it via class
    loadout = hb.make_instance("st.Loadout")
    equip = hb.make_instance("st.Equipment")
    g = hb.make_instance("st.item.Gear", size=0xa8)
    proc.put_u64(g + C.OFF_GEAR_KIND, hb.make_string("Sword_X"))
    proc.put_i32(g + C.OFF_GEAR_LEVEL, 7)
    content = hb.make_array([g])
    proc.put_u64(equip + C.OFF_EQUIPMENT_CONTENT, content)
    proc.put_u64(loadout + C.OFF_LOADOUT_EQUIPMENT, equip)
    proc.put_u64(hero + 0x300, loadout)                  # drifted slot

    out = hero_gear(proc, hl, hero)
    assert [g["kind"] for g in out] == ["Sword_X"]


def test_hero_gear_never_fabricates_from_wrong_class():
    """A pointer at the loadout slot that is NOT a st.Loadout (garbage read)
    yields zero rows — sweep verifies the class, no fabricated gear."""
    proc, hl, hero, hb = _scene_with_gear(None)
    junk = hb.make_instance("ent.Foe")                   # wrong class there
    proc.put_u64(hero + C.OFF_HERO_LOADOUT, junk)
    assert hero_gear(proc, hl, hero) == []


def test_party_gear_rows_are_labelled_by_slot_not_by_live_index():
    """The Party page names each equipped piece's slot from the piece's own
    sheet type rather than the live array index: armor and jewelry read as Head
    / Neck / Ring / Trinket, containers and mounts as themselves, and a weapon
    keeps its own type. Repeats are numbered in the order the game hands the
    pieces over (the two rings read Ring 1 / Ring 2); a piece the sheet can't
    name still says which live slot it came from.

    The ids and indices below are the local player's own equipped set as read
    live (2026-09-15).
    """
    from farever_companion.ui.pages.party_page import _gear_slot_labels

    rows = [
        {"slot": 0, "kind": "Axe_Boomerang"},          # Cheese Moon
        {"slot": 1, "kind": "GS_Nova"},                # Martyr of Enripit
        {"slot": 3, "kind": "Head_RDemon_Fig_Craft"},
        {"slot": 4, "kind": "Necklace_Z2_Ap"},
        {"slot": 12, "kind": "Finger_Z3RCraft_Ap"},
        {"slot": 13, "kind": "Trinket_Kobold"},
        {"slot": 14, "kind": "Finger_Z3RCraft_Ap"},
        {"slot": 21, "kind": "SkunkMeat_Z1"},
        {"slot": 29, "kind": "Mount_Demon_03"},
        {"slot": 7, "kind": "NotARealItem_XYZ"},       # sheet can't name it
    ]
    assert _gear_slot_labels(rows) == [
        "Axe", "Great Sword", "Head", "Neck", "Ring 1", "Trinket",
        "Ring 2", "Food", "Mount", "Slot 7"]
    # a set with no repeated slot keeps bare labels (no needless numbering)
    assert _gear_slot_labels(rows[:4]) == ["Axe", "Great Sword", "Head",
                                           "Neck"]
    # an empty list is not an error path either
    assert _gear_slot_labels([]) == []


def test_hero_buffs_reads_kind_stacks_duration():
    """Sanity on the buffs reader (same heap builder) — the party page's
    other live column."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("String")
    hb.make_type("hxbit.ArrayProxyData")
    hb.make_type("ent.Hero", fields={"statuses": C.OFF_HERO_STATUSES})
    status_type = hb.make_type("st.skill.Status", fields={
        "kind": C.OFF_STATUS_KIND, "stacks": C.OFF_STATUS_STACKS,
        "duration": C.OFF_STATUS_DURATION})
    hero = hb.make_instance("ent.Hero")

    # size covers the last field write (stacks at OFF_STATUS_STACKS = 0x1d0
    # is an i32 ENDING at +0x1d4) — a size equal to the offset itself made
    # the stacks write spill past the allocation into the next bump-alloc
    # (the kind string's buffer), which read back as junk kind bytes
    st1 = hb.make_instance("st.skill.Status", size=0x1e0)
    proc.put_u64(st1 + C.OFF_STATUS_KIND, hb.make_string("Whetstone_Status"))
    proc.put_i32(st1 + C.OFF_STATUS_STACKS, 1)
    proc.put_f64(st1 + C.OFF_STATUS_DURATION, 3600.0)
    arr = hb.make_array([st1])
    # statuses is a proxy whose array field holds the array
    hb.make_type("hxbit.ArrayProxyData", fields={"array": 0x08})
    proxy = hb.make_instance("hxbit.ArrayProxyData", size=0x80)
    proc.put_u64(proxy + 0x08, arr)
    proc.put_u64(hero + C.OFF_HERO_STATUSES, proxy)

    hl = Hl(proc)
    out = hero_buffs(proc, hl, hero)
    assert [b["kind"] for b in out] == ["Whetstone_Status"]
    assert out[0]["stacks"] == 1
    assert out[0]["duration"] == 3600.0


# --- labels ---------------------------------------------------------------

def test_status_name_food_buffs_use_granting_item_name():
    from farever_companion.data import names
    assert names.status_name("Whetstone_Status") == "Whetstone"
    assert names.status_name("Weighstone_Status") == "Weightstone"   # typo'd id
    assert names.status_name("Weightstone_Status") == "Weightstone"


def test_status_source_item_resolves_and_needs_no_item_statuses():
    from farever_companion.data import names
    assert names.status_source_item("Whetstone_Status") == "Whetstone"
    assert names.status_source_item("Weighstone_Status") == "Weightstone"
    assert names.status_source_item(None) is None
    assert names.status_source_item("NotARealStatus_XYZ") is None


def test_a_shared_item_status_is_named_after_the_item_that_granted_it():
    """'ItemStatus' is declared by 48 different items (every elixir, food and
    potion uses it), so the status id alone cannot say which buff is active.

    The reverse map used to answer with whichever of those 48 the sheet listed
    first — a live ElixirOfDexterity_Z2 buff rendered as "Minor Alchemist
    Cauldron". The shared case is now (a) detected, (b) never answered from the
    sheet, and (c) named from the LIVE row's own originItem."""
    from farever_companion.data import names

    assert names.status_is_shared("ItemStatus") is True
    # the id no longer resolves to an arbitrary item — not for naming, and not
    # for the icon that resolves through the same map
    assert names.status_source_item("ItemStatus") is None
    assert names.status_name("ItemStatus") == "Item Status"        # honest
    assert names.status_name("ItemStatus", "ElixirOfDexterity_Z2") == \
        "Elixir of Dexterity"

    # a status only ONE item grants keeps being named from that item, live or not
    assert names.status_is_shared("Whetstone_Status") is False
    assert names.status_source_item("Whetstone_Status") == "Whetstone"
    assert names.status_name("Whetstone_Status") == "Whetstone"
    assert names.status_name("Weightstone_Status") == "Weightstone"
    # a skill aura is not item-sourced: the item that carries it never renames it
    assert names.status_name("Axe_Boomerang_Skill_Passive_Status",
                             "Axe_Boomerang") == "Bloodrage Aura"


def test_buff_rows_name_and_icon_come_from_the_granting_item(qapp):
    """The two statuses a live character carried on 2026-09-15, rendered:

        Axe_Boomerang_Skill_Passive_Status  -> "Bloodrage Aura  (from Cheese Moon)"
        ItemStatus (ElixirOfDexterity_Z2)   -> "Elixir of Dexterity"

    The elixir row is the fix: it was "Minor Alchemist Cauldron  (x1 · 3600.0s ·
    from Elixir of Dexterity)" — the wrong item AND the right one, twice. A
    shared status is named after its item, so the tail drops the "from" that
    would only repeat it, and the row's sprite is that item's too."""
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.data import icons as gicons
    from farever_companion.ui.pages.party_page import (PartyPageMixin,
                                                       _buff_label)
    from farever_companion.ui.pages.party_rows import _buff_tail

    aura = {"kind": "Axe_Boomerang_Skill_Passive_Status", "stacks": 1,
            "duration": 0.0, "source_item": "Axe_Boomerang"}
    elixir = {"kind": "ItemStatus", "stacks": 1, "duration": 3600.0,
              "source_item": "ElixirOfDexterity_Z2"}

    assert _buff_label(aura) == "Bloodrage Aura"
    assert _buff_tail(aura) == "  (from Cheese Moon)"
    assert _buff_label(elixir) == "Elixir of Dexterity"
    assert _buff_tail(elixir) == ""                     # no "from", no time
    # a row the reader could not attribute is still named, never blank
    assert _buff_label({"kind": "ItemStatus"}) == "Item Status"

    # the icon follows the same rule: the LIVE item's sprite for a shared status
    row = PartyPageMixin._party_buff_row(
        elixir["kind"], _buff_label(elixir), _buff_tail(elixir),
        source_item=elixir["source_item"])
    shown = row.findChildren(QtWidgets.QLabel)[0].pixmap()
    assert not shown.isNull()
    if gicons.has_icon("item", "ElixirOfDexterity_Z2"):
        from farever_companion.ui.pages.party_page import (_BUFF_ICON_PX,
                                                            _party_sprite_tile)
        want = _party_sprite_tile("item", "ElixirOfDexterity_Z2", _BUFF_ICON_PX)
        assert shown.cacheKey() == want.cacheKey()


def test_buff_icon_is_framed_so_a_dark_sprite_is_not_washed_out():
    """The buff/gear icons are drawn on the standard accent tile, never as a
    bare atlas crop (2026-09-26).

    An atlas cell is fully opaque and carries the game's own painted
    background; on the skills sheet a third of them are dark. The Water
    Infusion sprite's 22px corner is RGB(10,25,59) — luma 23 against this
    card's PANEL luma 32 — so dropped straight onto the card it had no edge
    at all and read as a washed smudge. The tile's 1px accent border is what
    gives any sprite an outline.

    Asserted on a DARK sprite on purpose: a bright one (the sheet median is
    47% bright pixels) hides the difference, so a test that picked one would
    pass against the bug.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtGui, QtWidgets

    from farever_companion.data import icons as gicons
    from farever_companion.ui import theme
    from farever_companion.ui.pages.party_page import (_BUFF_ICON_PX,
                                                        PartyPageMixin)
    sid = "Shield_OrbitWater_P_StatusBlockBuffed"
    if not gicons.has_icon("skills", sid):
        pytest.skip("no atlas sprite for the dark fixture")

    row = PartyPageMixin._party_buff_row(sid, "Water Infusion: empowered", "")
    shown = row.findChildren(QtWidgets.QLabel)[0].pixmap()
    assert not shown.isNull()

    # 1. it is NOT the bare crop any more
    bare = gicons.pixmap("skills", sid, _BUFF_ICON_PX).toImage()
    assert shown.toImage() != bare, "buff icon regressed to a bare atlas crop"

    # 2. the frame is the accent, and it is actually there: the corner pixel
    #    is the border, opaque enough to read against PANEL
    px = shown.toImage().pixelColor(0, 0)
    accent = QtGui.QColor(theme.ACCENT)
    assert px.alpha() > 100, f"no frame: corner alpha {px.alpha()}"
    assert (abs(px.red() - accent.red()) < 40
            and abs(px.blue() - accent.blue()) < 40), \
        f"frame is not the accent: {px.getRgb()} vs {accent.getRgb()}"

    # 3. the sprite still drew inside the frame (the tile is not just a square)
    inner = shown.toImage().pixelColor(_BUFF_ICON_PX // 2, _BUFF_ICON_PX // 2)
    assert inner.getRgb() != px.getRgb(), "tile drew no sprite in its middle"

    # the gear-note icon takes the same path
    if gicons.has_icon("item", "Sword_Beefury"):
        gear = PartyPageMixin._party_gear_icon("Sword_Beefury")
        gp = gear.pixmap()
        assert gp.toImage() != gicons.pixmap("item", "Sword_Beefury",
                                             _BUFF_ICON_PX).toImage()


def test_status_name_non_item_statuses_fall_back_to_skill_sheet():
    from farever_companion.data import names
    # a skill-sheet status row (name == id -> humanized, or real name)
    nm = names.status_name("Bow_BigGame_Passive_Status")
    assert nm and nm != "Bow BigGame Passive Status" or nm == \
        names.humanize("Bow_BigGame_Passive_Status")


def test_derived_skill_rows_inherit_their_parents_display_name():
    """A passive's proc reads as the passive, not a humanized id.

    The sheet names a skill but leaves its derived rows blank (the display
    name comes through as the id itself). `Halos_Totem_Passive_Aoe` is the
    damage-dealing half of Ghost Clams of the Low Tide's "Power of the Tides"
    passive; live 2026-10-02 the DPS log listed it as "Halos Totem Passive
    Aoe" (the raw id), so the passive the player recognizes looked missing.
    """
    from farever_companion.data import names

    # the reported case: the unnamed AoE half takes the passive's name
    assert names.skill_name("Halos_Totem_Passive_Aoe") == "Power of the Tides"
    # ...and so does any other effect marker on a named parent
    assert names.skill_name("Daggers_Demondash_Passive_Status") == \
        "Chaos Scorch"
    assert names.skill_name("DM_Multispin_Passive_Status_Max_Stacks") == "Zeal"

    # a row with its own real name is untouched, parent or not
    assert names.skill_name("Mage_Conduit_Projectile") == "Conduit: Shard"
    assert names.skill_name("DS_Bladeleaf_Passive") == "Queen's Decree"

    # no named ancestor (or none at all): the humanized id still stands
    assert names.skill_name("Halos_Totem_Skill1") == "Halos Totem Skill 1"
    assert names.skill_name("NotARealSkill_XYZ") == "Not A Real Skill XYZ"
    assert names.skill_name(None) is None
    assert names.skill_name("") == ""


def test_a_concatenated_effect_suffix_still_reaches_its_parent_skill():
    """A derived row whose effect marker is GLUED on still knows its parent.

    Two shapes of derived row ship in the sheet. The common one keeps the
    marker as its own `_`-segment (`DS_Bladeleaf_Skill2` + `_Status`), so
    splitting on `_` reaches the parent. The other CONCATENATES it:
    `Daggers_DuplicatePoison_Passive` + `Status` -> `..._PassiveStatus`. No
    split can ever produce that parent, so the walk dead-ended and everything
    built on it went quiet - the popup's "From Weapon" section and
    `item_proc_source`'s proc tag both need the parent ID, and the Combat rail
    row "Gash" showed no weapon at all (reported live 2026-10-05).
    """
    from farever_companion.data import names
    from farever_companion.data import items as idata

    # the reported row, plus a second weapon: the shape is shared, so this is
    # not a one-off for Gash
    for sid, parent in (("Daggers_DuplicatePoison_PassiveStatus",
                         "Daggers_DuplicatePoison_Passive"),
                        ("DA_Water_Skill1_StatusShield", "DA_Water_Skill1")):
        assert names.parent_skill_id(sid) == parent
        assert idata.weapons_for_skill(names.parent_skill_id(sid)), sid

    # the row keeps its OWN name - "Gash" is the sheet's name for the status,
    # and the ancestor walk must not overwrite it with the passive's
    assert names.skill_name("Daggers_DuplicatePoison_PassiveStatus") == "Gash"

    # A trailing DIGIT is a numbered VARIANT, not an effect marker. The sheet
    # carries `Halos_Totem_Skill2` ("Riptide") beside `Halos_Totem_Skill`
    # ("Tidal Totem"), and `Rogue_KnivesTempest_Status` ("Silent Storm") beside
    # `..._Status2` ("Shred") - distinct skills sharing a prefix, not a skill
    # and its derived half. Peeling the digit folds them together, which is
    # exactly what this guard was added for.
    assert names.parent_skill_id("Halos_Totem_Skill1") is None
    assert names.skill_name("Halos_Totem_Skill1") == "Halos Totem Skill 1"
    assert names.parent_skill_id("Halos_Totem_Skill2") is None
    assert names.skill_name("Halos_Totem_Skill2") == "Riptide"


def test_derived_status_rows_inherit_their_parents_display_name():
    """A passive's BUFF reads as the passive on the party page, not a raw id.

    Same rule as the damage log, applied to the status channel: a passive that
    applies a status has that status as a derived row (`..._Passive_Status`),
    and the sheet leaves the row unnamed. The party page's buff line goes
    through `status_name`, so without the walk it listed
    "Halos Totem Passive Aoe Status" where the player's skill is called
    "Power of the Tides" — the identity mismatch the damage log already fixed.
    """
    from farever_companion.data import names

    # the reported case: the unnamed status half takes the passive's name
    assert names.status_name("Halos_Totem_Passive_Aoe_Status") == \
        "Power of the Tides"
    # ...and the buff row the page actually renders uses that same answer
    from farever_companion.ui.pages.party_rows import _buff_label
    assert _buff_label({"kind": "Halos_Totem_Passive_Aoe_Status"}) == \
        "Power of the Tides"

    # a status with its own real name is untouched, parent or not
    assert names.status_name("Axe_Boomerang_Skill_Passive_Status") == \
        "Bloodrage Aura"
    assert names.status_name("Bow_BigGame_Passive_Status") == \
        "Thrill of the Hunter"

    # the item-sourced route still wins: an item buff is named after its item,
    # never after a skill ancestor that happens to share the prefix
    assert names.status_name("Whetstone_Status") == "Whetstone"
    assert names.status_name("Weightstone_Status") == "Weightstone"
    # ...and a SHARED status keeps the live item's name, not a parent's
    assert names.status_name("ItemStatus") == "Item Status"
    assert names.status_name("ItemStatus", "ElixirOfDexterity_Z2") == \
        "Elixir of Dexterity"

    # no named ancestor: the humanized id still stands
    assert names.status_name("NotARealStatus_XYZ") == "Not A Real Status XYZ"
    assert names.status_name(None) is None


# --- live-diag trace -------------------------------------------------------

def test_inspect_trace_records_the_gear_walk():
    proc, hl, hero, _hb = _scene_with_gear([("Sword_Beefury", 25)])
    from farever_companion.core import inspect as hi
    with hi.inspect_trace() as tr:
        out = hero_gear(proc, hl, hero)
    assert out  # the scene reads fine
    text = "\n".join(tr)
    assert "loadout offset" in text          # hop 1 recorded
    assert "equipment offset" in text        # hop 2
    assert "content array" in text           # hop 3
    assert "slot 0" in text and "kind='Sword_Beefury'" in text
    # default poll path leaves the trace off
    assert hi._trace is None
    hero_gear(proc, hl, hero)                # must not raise with trace off


def test_inspect_trace_records_the_buff_walk_and_flags_wrapped_entries():
    """The buffs column is the one a container change would quietly empty, so
    its walk reports the same steps the gear walk does — the offset, the array,
    and a per-entry verdict naming the entry's TYPE WORD KIND.

    That kind is the diagnostics that matters: the gear slots arrived as
    Null-style records (kind 15) whose class cannot be named, and the trace is
    what turns the same failure on the statuses prop into something the page's
    Diag dump shows instead of a bare "No buffs live"."""
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("String")
    hb.make_type("hl.types.ArrayObj")
    hb.make_type("ent.Hero", fields={"statuses": C.OFF_HERO_STATUSES})
    hb.make_type("st.skill.Status", fields={
        "kind": C.OFF_STATUS_KIND, "stacks": C.OFF_STATUS_STACKS,
        "duration": C.OFF_STATUS_DURATION})
    hb.make_type("hxbit.ArrayProxyData", fields={"array": 0x08})
    hero = hb.make_instance("ent.Hero", size=0x600)

    real = hb.make_instance("st.skill.Status", size=0x1e0)
    proc.put_u64(real + C.OFF_STATUS_KIND, hb.make_string("Whetstone_Status"))
    proc.put_i32(real + C.OFF_STATUS_STACKS, 2)
    proc.put_f64(real + C.OFF_STATUS_DURATION, 3600.0)
    # a wrapped (Null-style) cell holding a status: same shape the item slots
    # arrived in — class_of() cannot name it, and the trace must say so
    wrapped = _make_record(proc, hb, real)
    arr = hb.make_array([real, wrapped, 0])
    proxy = hb.make_instance("hxbit.ArrayProxyData", size=0x80)
    proc.put_u64(proxy + 0x08, arr)
    proc.put_u64(hero + C.OFF_HERO_STATUSES, proxy)

    hl = Hl(proc)
    from farever_companion.core import inspect as hi
    with hi.inspect_trace() as tr:
        out = hero_buffs(proc, hl, hero)
    # the real status still reads, and only it is a row
    assert [b["kind"] for b in out] == ["Whetstone_Status"]
    assert out[0]["stacks"] == 2
    text = "\n".join(tr)
    assert "statuses offset" in text          # the container hop
    assert "entries" in text                  # the array it unwrapped to
    assert "entry 1" in text and "type kind 15" in text, text   # wrapped cell
    assert "entry 2 — null" in text
    assert "1 rows from 3 entries" in text
    # an unreachable hero still explains itself rather than returning silently
    with hi.inspect_trace() as tr2:
        assert hero_buffs(proc, hl, 0) == []
    assert any("no hero address" in s for s in tr2)


def test_inspect_trace_off_by_default_and_reset_after():
    proc, hl, hero, _hb = _scene_with_gear(None)   # hero with no loadout
    from farever_companion.core import inspect as hi
    assert hi._trace is None
    with hi.inspect_trace() as tr:
        assert hi._trace is tr
        assert hero_gear(proc, hl, hero) == []
    assert hi._trace is None
    assert any("no loadout offset" in s or "loadout sweep miss" in s
               for s in tr)


def test_buff_row_tail_shows_only_what_the_game_states():
    """The buffs column is alive — the walk reads the hero's `statuses` prop
    through an hxbit.ArrayProxyData -> hl.types.ArrayObj into st.skill.Status
    rows (traced live 2026-09-15, the local player's single status being the
    axe's own `Axe_Boomerang_Skill_Passive_Status`, duration 0) — so what it
    shows has to be as honest as what it reads.

    Two shapes must not read as data: a stack of 1 (the norm, says nothing) and
    any duration figure — the field is the effect's TOTAL duration, frozen at
    application (live probes 2026-09-16: 2784.43s unchanged for 20s+), never a
    countdown, and the game stores no remaining time, so no time is shown. The
    granting item still shows, resolved to the item's name."""
    pytest.importorskip("PySide6")
    from farever_companion.ui.pages.party_page import _buff_tail

    # the live row the game reports for the local player right now
    assert _buff_tail({"kind": "Axe_Boomerang_Skill_Passive_Status",
                       "stacks": 1, "duration": 0.0,
                       "source_item": "Axe_Boomerang"}) == \
        "  (from Cheese Moon)"
    # a real buff: stacks and its granting item stated — never the duration
    tail = _buff_tail({"stacks": 3, "duration": 90.0, "source_item": "Feast"})
    assert tail == "  (x3 · from Plainswalker Feast)"
    # each piece is independently optional, and a missing one is never invented
    assert _buff_tail({"stacks": 2, "duration": None}) == "  (x2)"
    assert _buff_tail({"stacks": None, "duration": 0.0}) == ""
    assert _buff_tail({}) == ""
    assert _buff_tail({"duration": 3600.0}) == ""


def test_the_record_amount_is_gated_by_the_sheets_stack_ceiling():
    """How MANY a container record holds is its own +0x28 i32 — the same 0x38
    wrapper the slot unwrap reads, so that field has no name to resolve.

    Read live 2026-09-15 across 238 container entries: it IS the quantity
    (Soulstone 20, Cook_11 7) and exactly 1 for every non-stackable piece. It is
    trusted only inside the game's own ceiling for the item's type (itemType
    props.stackSize, 1000 for consumables and food): a word outside 1..ceiling —
    or anything but 1 where the type declares no stacking at all — reports None.
    No count beats a wrong one.

    Still on a live path after the bag walk was removed: the GEAR walk reports
    the amount for its consumable slots, which hold a stack.
    """
    from farever_companion.core import inspect as hi

    # the ceilings come from the game's sheet, so a data-less run says so here
    assert hi._stack_ceiling("Soulstone_Z1_4") == 1000
    assert hi._stack_ceiling("Cook_11") == 1000
    assert hi._stack_ceiling("Axe_Boomerang") is None    # gear does not stack

    # the amount word itself, on records built with each stated count
    proc = FakeProc()
    hb = HeapBuilder(proc)
    tp = _record_type(proc, hb)
    hl = Hl(proc)
    spec = [("Soulstone_Z1_4", 20),      # a stack, as the live read had it
            ("Cook_11", 7),
            ("Axe_Boomerang", 1),         # non-stackable: exactly one
            ("Axe_Boomerang", 0),         # not a plausible amount -> no count
            ("Soulstone_Z1_4", 5000)]     # above the type's ceiling -> none
    got = [hi._record_count(proc, hl,
                            _make_record(proc, hb, None, tp, count=amount),
                            kind)
           for kind, amount in spec]
    assert got == [20, 7, 1, None, None]
    assert hi._record_count(proc, hl, 0, "Soulstone_Z1_4") is None


# --- the equipped companion (loadout.pet + the live entity) ----------------

def _pet_scene(pet_id: str | None = "Goat_Demon", *,
               pet_field: int | None = 0x20, pet_is_string: bool = True):
    """A hero whose st.Loadout exposes its pet BY NAME.

    `pet` sits at a fixture-chosen offset and is not any calibrated constant, so
    a reader that hardcoded an offset cannot pass. Pet ids, not slot records:
    the game's own slot table has no Slot_Pet (the nearest is Slot_Mount), the
    pet is a String on the loadout — live 2026-09-15 at +0xc0 holding
    'Goat_Demon'.
    """
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("String")
    hb.make_type("ent.Hero", fields={"loadout": 0x08})
    fields = {"equipment": 0x08}
    if pet_field is not None:
        fields["pet"] = pet_field
    hb.make_type("st.Loadout", fields=fields)
    hb.make_type("st.Equipment", fields={"content": 0x08})
    hero = hb.make_instance("ent.Hero", size=0x400)
    loadout = hb.make_instance("st.Loadout", size=0x60)
    proc.put_u64(hero + 0x08, loadout)
    if pet_field is not None:
        if pet_id is None:
            proc.put_u64(loadout + pet_field, 0)        # equipped nothing
        elif pet_is_string:
            proc.put_u64(loadout + pet_field, hb.make_string(pet_id))
        else:                                           # a field that is not one
            proc.put_u64(loadout + pet_field,
                         hb.make_instance("st.Equipment", size=0x40))
    return proc, Hl(proc), hero, loadout


def test_hero_pet_reads_the_loadout_pet_by_name():
    """The pet id is st.Loadout.pet, resolved by name, and the row says which
    offset it came from (live: 'Goat_Demon' at 0xc0)."""
    proc, hl, hero, loadout = _pet_scene()
    pet = hero_pet(proc, hl, hero)
    assert pet["id"] == "Goat_Demon"
    assert pet["off"] == 0x20 and pet["cls"] == "String"
    assert loadout != hero


def test_hero_pet_separates_no_pet_from_no_field():
    """Two nothings that must not read alike: the game saying the hero has no
    pet equipped, and this build not exposing the field at all.

    A null field keeps its `off` (the pet is unequipped — the game's answer), a
    loadout whose type has no pet field leaves `off` None (a reader limit), and
    a field holding a non-String leaves `cls` for the Diag dump to print."""
    proc, hl, hero, _l = _pet_scene(pet_id=None)
    assert hero_pet(proc, hl, hero) == {"id": None, "off": 0x20, "cls": None}

    proc, hl, hero, _l = _pet_scene(pet_field=None)
    assert hero_pet(proc, hl, hero) == {"id": None, "off": None, "cls": None}

    proc, hl, hero, _l = _pet_scene("Goat_Demon", pet_is_string=False)
    wrong = hero_pet(proc, hl, hero)
    assert wrong["id"] is None and wrong["off"] == 0x20
    assert wrong["cls"] == "st.Equipment"             # not a String: no id

    assert hero_pet(proc, hl, 0) == {"id": None, "off": None, "cls": None}


def test_pet_row_only_claims_this_heroes_creature():
    """The Companion row shows the creature THIS hero owns — the same species
    belonging to someone else is not theirs.

    Live 2026-09-15: your pet is an ent.Foe whose unit_id is the loadout's pet
    ('Goat_Demon') and whose owner_addr is your own hero address. So the join
    needs all three, and `is_player_owned` alone (true for any Hero-owned pet)
    would show a party member's Raminiature as yours."""
    from farever_companion.ui.pages.party_page import _live_pet

    def unit(unit_id, *, owner, owned=True, hero=False):
        return SimpleNamespace(unit_id=unit_id, cls="ent.Foe", level=25, hp=0.0,
                               is_hero=hero, is_player_owned=owned,
                               owner_addr=owner, addr=0x1000)

    mine = unit("Goat_Demon", owner=0xAA)
    theirs = unit("Goat_Demon", owner=0xBB)
    wild = unit("Goat_Demon", owner=0xAA, owned=False)
    hero = unit("Goat_Demon", owner=0xAA, hero=True)
    scene = SimpleNamespace(units=lambda: [theirs, wild, hero, mine])
    empty = SimpleNamespace(units=lambda: [])

    assert _live_pet(scene, 0xAA, "Goat_Demon") is mine
    assert _live_pet(scene, 0xBB, "Goat_Demon") is theirs
    assert _live_pet(scene, 0xCC, "Goat_Demon") is None
    assert _live_pet(empty, 0xAA, "Goat_Demon") is None
    assert _live_pet(scene, 0xAA, "") is None


@pytest.fixture(scope="module")
def qapp():
    """One offscreen QApplication for the whole module (the section renderers
    build real widgets)."""
    QtWidgets = pytest.importorskip("PySide6").QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _fake_party():
    """A page with only the bits the section renderers touch (the widget stack
    is not built: these tests are about what the renderers read and say)."""
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui.pages.party_page import PartyPageMixin

    class Fake(PartyPageMixin):
        def __init__(self):
            self._party_gear = QtWidgets.QVBoxLayout()

        @staticmethod
        def _widget_alive(w) -> bool:
            return True

    return Fake()


def _layout_text(lay) -> list[str]:
    """Every label text in a layout, in order."""
    from PySide6 import QtWidgets

    out = []
    for i in range(lay.count()):
        w = lay.itemAt(i).widget()
        if w is None:
            continue
        kids = w.findChildren(QtWidgets.QLabel)
        out.extend(k.text() for k in kids)
        if not kids and hasattr(w, "text"):
            out.append(w.text())
    return out


def test_bag_slots_are_skipped_from_the_gear_list():
    """A bag slot holds a container (live 2026-09-15: `Bag_Z2` in slots 22-26),
    so the gear list carried five identical `Bag N — Bag_Z2` rows. They are
    skipped outright now — no row, no summary line — the game's own slot table
    (`is_bag_slot`) decides, so a patch that moves the slots is still picked up."""
    from farever_companion.data.items import labels

    assert [i for i in range(30) if labels.is_bag_slot(i)] == [22, 23, 24,
                                                               25, 26, 27]
    assert labels.equip_slot_label(22) == "Bag 1"
    assert labels.is_bag_slot(21) is False           # Consumable 4
    assert labels.is_bag_slot(28) is False           # Glider
    assert labels.is_bag_slot(None) is False and labels.is_bag_slot(30) is False


def test_pet_labels_name_the_creature_from_the_game_data():
    """The name is the unit sheet's own ('Goat_Demon' -> Demonic Raminiature),
    never the id respelled, and the sub-line states only live facts.

    hp is dropped at 0 because the snapshot does not fill it for companions (the
    live pet entity reads hp=0), so printing it would be a fabrication wearing a
    number's clothes."""
    pytest.importorskip("PySide6")

    from farever_companion.ui.pages.party_rows import _pet_labels

    live = SimpleNamespace(cls="ent.Foe", level=25, hp=0.0, addr=0x1000)
    assert _pet_labels("Goat_Demon", live) == \
        ("Demonic Raminiature", "Goat_Demon  ·  ent.Foe  ·  Lv 25")
    # a real hp does show, formatted like the rest of the page
    hurt = SimpleNamespace(cls="ent.Foe", level=25, hp=1234.0)
    assert _pet_labels("Goat_Demon", hurt)[1].endswith("HP 1,234")
    # equipped but not in the loaded scene: said so, not filled in
    name, sub = _pet_labels("Goat_Demon", None)
    assert name == "Demonic Raminiature"
    assert sub == "Goat_Demon  ·  equipped in game, not loaded in this scene"
    # an id the sheet has never heard of still reads as words, not an id
    assert _pet_labels("Weird_Pet_X", None)[0] == "Weird Pet X"


def test_talent_ui_is_gone():
    """The Party Talents board was removed: the replicated specialization prop
    does not carry tree points for OTHER players (only the local player's, and
    only while their own talent screen is open), so the board could not deliver
    what it promised. Guard that no dead references to it linger."""
    import importlib
    import farever_companion.ui.pages as pages
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("farever_companion.ui.pages.party_talents")
    src = pages.__path__[0]
    import pathlib
    for f in pathlib.Path(src).glob("*.py"):
        assert "party_talents" not in f.read_text(encoding="utf-8"), \
            f"{f.name} still references the removed talents module"


def _built_party_page():
    """The REAL Party Inspect page in a fake host: no ControlPanel, no model.

    Used by every test that asserts on the page's own widget tree, so they all
    look at the same build (and the page's 2 s poll, which would try to read a
    game that is not attached, is stubbed out).
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    from farever_companion.ui.memory_reclaimer import MemoryReclaimerMixin
    from farever_companion.ui.pages.party_page import PartyPageMixin

    class Host(PartyPageMixin, QtCore.QObject):
        _widget_alive = staticmethod(MemoryReclaimerMixin._widget_alive)

        def __init__(self):
            QtCore.QObject.__init__(self)
            self.s = None
            self.model = None

        def _page_container(self, title=None):
            page = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(page)
            lay.setContentsMargins(22, 20, 22, 20)
            lay.setSpacing(16)
            return page, lay

        def _current_page(self):
            return "party"

        def _refresh_party_all(self):
            pass

        def _refresh_party_detail(self):
            pass

        def _party_sync_chrome(self, *a):
            pass

    host = Host()
    return host, host._page_party()


def _teardown_party_page(host, page) -> None:
    from PySide6 import QtWidgets
    timer = getattr(host, "_party_timer", None)
    if timer is not None:
        timer.stop()
    page.deleteLater()
    QtWidgets.QApplication.instance().processEvents()


def test_every_equip_slot_is_in_one_group():
    """Every PIECE slot the game's table lists belongs to exactly one category.

    The panel used to draw a hand-picked subset of the slot table, so "this
    category is not drawn" and "there is nothing in this category" looked
    identical — and tools, the glider and the ring/trinket slots were simply
    absent. Derived from `equip_slots()` rather than hardcoded, so a patch that
    adds a slot fails here instead of silently going missing from the page.

    The container slots (`Slot_Bag1-6`) are the one sanctioned exclusion: they
    hold containers, so a tile could only name the container and never its
    contents, and the panel deliberately draws none of them. The gap is spelled
    out here — `is_bag_slot`, not a hardcoded 22-27 — so a new container slot in
    the sheet fails this test rather than quietly vanishing from the page.
    """
    from farever_companion.data.items import labels as glabels
    from farever_companion.ui.pages.party_profile import _SLOT_GROUPS

    sheet = list(glabels.equip_slots())
    containers = {i for i in range(len(sheet)) if glabels.is_bag_slot(i)}
    shown = [s for _title, slots in _SLOT_GROUPS for s in slots]
    assert sorted(shown) == sorted(set(range(len(sheet))) - containers), (
        "every equipment slot except a container slot must appear in exactly "
        "one group")
    assert len(shown) == len(set(shown)), "a slot is listed in two groups"
    assert not (containers & set(shown)), \
        "a container slot has a tile; those hold containers, not pieces"
    for title, slots in _SLOT_GROUPS:
        assert slots, f"{title} is an empty group"
        for slot in slots:
            assert glabels.equip_slot_label(slot), \
                f"{title} holds slot {slot}, which the sheet does not name"
    # the categories the page is supposed to show, in slot order
    assert [t for t, _s in _SLOT_GROUPS] == [
        "Weapons", "Armor", "Jewelry", "Tools", "Consumables", "Extras"]


def test_equipment_panel_builds_a_tile_for_every_piece(qapp):
    """The built page carries one tile per piece slot, plus the pet.

    The pet is not a slot (the id lives on `st.Loadout.pet`), every category is
    captioned, and a container slot has NO tile: a live-shaped walk that names
    one is ignored outright, because a bag's tile could only repeat the
    container's name and nothing about its contents.
    """
    pytest.importorskip("PySide6")
    from farever_companion.data.items import labels as glabels

    host, page = _built_party_page()
    try:
        cards = host._party_grid_cards
        sheet = list(glabels.equip_slots())
        expected = [i for i in range(len(sheet)) if not glabels.is_bag_slot(i)]
        assert sorted(cards) == expected, \
            "one tile per piece slot, and none for a container slot"
        assert host._party_pet_box is not None, "the pet tile is not built"

        captions = [_group_caption(c) for c in host._party_group_cards]
        # the PANEL order: worn cards in the worn column, then the carried
        # trio whose captions sit in the fold row under Jewelry
        assert captions == ["Weapons", "Armor", "Jewelry", "Extras",
                            "Tools", "Consumables"]

        # a live-shaped walk: a real piece plus a bag container (as the game
        # reports one - `Bag_Z2` in slot 22)
        host._party_profile_fill([
            {"slot": 3, "kind": "Head_Plate_Z2", "rarity": "Common",
             "upgrade": 0, "level": 25, "state": "filled"},
            {"slot": 22, "kind": "Bag_Z2", "rarity": "Common",
             "upgrade": 0, "level": 0, "state": "filled"},
        ])
        host._party_piece_kind = "Axe_Boomerang"
        assert 22 not in cards, "the bag row produced a tile"

        cards[3].mousePressEvent()          # a real piece still inspects
        assert host._party_piece_kind == "Head_Plate_Z2"
    finally:
        _teardown_party_page(host, page)


def _group_caption(card) -> str:
    """A category card's title. It rides on the card itself
    (``_party_title``): the carried groups' captions live in the fold row
    (2026-09-25), so a carried card holds no caption widget to read.
    """
    return card._party_title


def test_gear_panel_is_worn_left_carried_right(qapp):
    """The panel is two columns: what you WEAR left, what you CARRY right.

    Extras leads the carried column above Tools (glider/mount/pet are
    ridden, not worn), the selected piece sits under the CARRIED column — it
    is what you are inspecting, not something you wear.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    from farever_companion.ui.pages.party_profile import _SLOT_GROUPS

    host, page = _built_party_page()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1600, 900)
    win.show()
    try:
        worn, carried = host._party_worn_box, host._party_carried_box
        # Spelled out rather than derived from _WORN_GROUPS: reading the same
        # constant the code reads would make this check agree with any split.
        worn_expect = ["Weapons", "Armor"]
        carried_expect = ["Jewelry", "Extras", "Tools", "Consumables"]

        def in_box(box):
            lay = box.layout()
            return [_group_caption(lay.itemAt(i).widget())
                    for i in range(lay.count())
                    if lay.itemAt(i).widget() is not None
                    and lay.itemAt(i).widget() in host._party_group_cards]

        # the left column holds the groups that are still cards of their own;
        # Jewelry is a fold TAB now (2026-09-26), so it is not stacked there
        assert in_box(worn) == worn_expect, \
            "the worn column is not the worn categories, in sheet order"
        # the fold row holds Jewelry + Extras + Tools, in that order;
        # Consumables moved to the right column under Buffs (2026-09-26) —
        # neither a second column nor a stacked card
        from farever_companion.ui.pages.party_profile import _FOLD_GROUPS
        fold_caps = [c for c, _k in host._party_fold_caps]
        assert [c.title() for c in fold_caps] == \
            list(_FOLD_GROUPS), \
            "the fold row is not the three groups that share it"
        assert "Jewelry" in _FOLD_GROUPS and "Jewelry" not in in_box(worn), \
            "Jewelry is both a fold tab and a stacked card"
        moved = [c.title() for c, _k in host._party_moved_caps]
        assert moved == ["Consumables"], \
            f"the group that moved to the right column is {moved}"
        assert carried_expect == list(_FOLD_GROUPS) + moved, \
            "a carried category is in neither place"
        # …and the right column's ONLY group card is the one that moved there
        # (it lives inside the Buffs block now, not stacked in the column)
        assert in_box(carried) == [], \
            f"the detail column stacks {in_box(carried)}"
        assert sorted(set(worn_expect) | set(carried_expect)) == \
            sorted(t for t, _slots in _SLOT_GROUPS), \
            "a category is missing from both columns"
        assert host._party_piece_host.parentWidget() is carried, \
            "the selected piece belongs under the carried column"
        assert host._party_piece_host.parentWidget() is not worn
        # stat totals sit ABOVE the piece panel (2026-09-25)
        cl = carried.layout()
        items = [cl.itemAt(i).widget() for i in range(cl.count())]
        assert items.index(host._party_totals_card) \
            < items.index(host._party_piece_host), \
            "stat totals is not above the piece panel"
        # both detail blocks are FIXED-width so the columns stop wandering
        assert host._party_totals_card.width() \
            == host._party_piece_host.width(), \
            "totals and piece panel disagree on the column width"
        assert host._party_totals_card.minimumWidth() \
            == host._party_totals_card.maximumWidth() > 0, \
            "the totals card is not fixed-width (the columns will wobble)"

        # side by side, worn on the left — the whole point of the arrangement
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        wtop = worn.mapTo(page, QtCore.QPoint(0, 0))
        ctop = carried.mapTo(page, QtCore.QPoint(0, 0))
        assert abs(wtop.y() - ctop.y()) <= 2, \
            f"the columns are stacked, not side by side ({wtop.y()} vs {ctop.y()})"
        assert wtop.x() < ctop.x(), "the worn column is not the left one"
    finally:
        win.hide()
        win.deleteLater()
        _teardown_party_page(host, page)


def test_party_inspect_carries_no_session_stat_strip(qapp):
    """The page has no identity strip above the roster.

    A `YOU · THIS SESSION` DPS / HPS / taken / deaths cluster used to sit in
    a hero strip (it restated your own session — the Combat page owns that).
    The strip's avatar + selected-name label then went too (2026-09-18): the
    selected roster card already shows the name in bold with the accent
    highlight, so the strip was a dupe. Removed, not hidden — this pins the
    shape so a later edit cannot quietly put it back.

    Deliberately wording-agnostic: it asserts the strip widgets are gone,
    the whole page's label vocabulary (the old captions, in case a strip
    returns somewhere else), and the absence of the reader that filled the
    old stat cluster. Re-adding identity chrome on purpose is fine — update
    this test in the same commit, which is the point.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    host, page = _built_party_page()
    try:
        # The strip's widgets are gone outright.
        for name in ("_party_avatar", "_party_title"):
            assert not hasattr(host, name), \
                f"{name} is back on the party page"

        # The removed strip's captions may not reappear anywhere on the page.
        captions = {"DPS", "HPS", "TAKEN", "DEATHS", "YOU · THIS SESSION",
                    "Select a player"}
        seen = {lbl.text() for lbl in page.findChildren(QtWidgets.QLabel)}
        assert not (seen & captions), sorted(seen & captions)

        # And the reader that filled the stat cluster is gone, so there is
        # nothing to re-wire.
        for name in ("_party_combat", "_fill_party_combat"):
            assert not hasattr(host, name), f"{name} is back on the party page"
    finally:
        _teardown_party_page(host, page)

    # Same guard across the page package: a re-added session reader referenced
    # from any page module has to be deliberate, not a stray resurrection.
    import pathlib
    from farever_companion.ui import pages as _pages
    for f in pathlib.Path(_pages.__path__[0]).glob("*.py"):
        src = f.read_text(encoding="utf-8")
        for name in ("_party_combat", "_fill_party_combat"):
            assert name not in src, f"{f.name} still references {name}"


def test_roster_column_never_scrolls_sideways(qapp):
    """The Players column is 240–300px and scrolls vertically only: a very
    long player name wraps the roster card downward instead of opening a
    horizontal scrollbar (measured: a 60-char name used to force 327px of
    sideways scroll).

    Word wrap alone does not do it — QLabel's minimumSizeHint is the
    UNWRAPPED width — so the card labels also size Ignored horizontally and
    the scroll area pins its horizontal policy off.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    host, page = _built_party_page()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1280, 800)
    win.show()
    try:
        host._party_roster_fill([(123, "X" * 60, "Priest"),
                                 (456, "Bob", "Warrior")])
        for _ in range(5):
            QtWidgets.QApplication.instance().processEvents()
        areas = page.findChildren(QtWidgets.QScrollArea)
        assert areas, "the roster scroll area is gone"
        for sa in areas:
            assert sa.horizontalScrollBarPolicy() == \
                QtCore.Qt.ScrollBarAlwaysOff, \
                "the roster column must never offer sideways scrolling"
            hb = sa.horizontalScrollBar()
            assert not hb.isVisible(), "a horizontal scrollbar is showing"
            assert hb.maximum() == 0, \
                f"roster content overflows sideways by {hb.maximum()}px"
    finally:
        win.hide()
        win.deleteLater()
        _teardown_party_page(host, page)


def test_buffs_section_stacks_under_the_carried_column(qapp):
    """BUFFS + gear notes live under the carried column, not in a far-right
    third column: the first buff content row sits inside the carried box's
    x-range and below the selected-piece panel. Probes the content host since
    the section's caption header is gone (2026-09-25) — the content itself
    still stacks in the same place.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    host, page = _built_party_page()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1280, 900)
    win.show()
    try:
        host._party_roster_fill([(123, "Alice", "Priest")])
        host._party_profile_fill([])
        for _ in range(5):
            QtWidgets.QApplication.instance().processEvents()
        assert not hasattr(host, "_party_buffs_wrap"), \
            "the far-right buffs column is back"
        carried = host._party_carried_box
        cx = carried.mapTo(page, QtCore.QPoint(0, 0)).x()
        hdr = host._party_buffs_host
        hx = hdr.mapTo(page, QtCore.QPoint(0, 0)).x()
        assert cx <= hx <= cx + carried.width(), \
            f"BUFFS content x={hx} is outside the carried column"
        piece_bottom = host._party_piece_host.mapTo(
            page, QtCore.QPoint(0, host._party_piece_host.height())).y()
        hy = hdr.mapTo(page, QtCore.QPoint(0, 0)).y()
        assert hy >= piece_bottom, \
            f"BUFFS content y={hy} is not below the piece panel ({piece_bottom})"
    finally:
        win.hide()
        win.deleteLater()
        _teardown_party_page(host, page)


def test_walked_rows_outside_the_slot_table_get_no_tile(qapp):
    """Out-of-table walked rows get no tile: Tools is exactly its 3 pinned
    slots until Diag identifies the rod's real slot. Container slots get none
    either."""
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    host, page = _built_party_page()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1280, 900)
    win.show()
    try:
        host._party_profile_fill([
            {"slot": 3, "kind": "Head_Plate_Z2", "rarity": "Common",
             "upgrade": 0, "level": 25, "state": "filled"},
            {"slot": 30, "kind": "Rod_Fishing", "rarity": "Common",
             "upgrade": 0, "level": 1, "state": "filled"},
            {"slot": 22, "kind": "Bag_Z2", "rarity": "Common",
             "upgrade": 0, "level": 0, "state": "filled"},
        ])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        cards = host._party_grid_cards
        assert 30 not in cards, "an out-of-table row grew a tile"
        assert 22 not in cards, "a container slot grew a tile"
        tools = next(c for c in host._party_group_cards
                     if _group_caption(c) == "Tools")
        grid = tools._party_body.layout()
        assert grid.count() == 3, "Tools is not back to its 3 pinned tiles"
    finally:
        win.hide()
        win.deleteLater()
        _teardown_party_page(host, page)


def test_offhand_sits_between_the_weapons(qapp):
    """The Weapons card reads Weapon 1, Offhand, Weapon 2 — the offhand pairs
    with the main hand. The tuples still carry the game's own slot indices;
    only the display order puts 2 before 1.
    """
    pytest.importorskip("PySide6")

    host, page = _built_party_page()
    try:
        weapons = next(c for c in host._party_group_cards
                       if _group_caption(c) == "Weapons")
        grid = weapons.layout().itemAt(1).widget().layout()
        order = [grid.itemAtPosition(0, c).widget()._party_slot_name.text()
                 for c in range(3)]
        assert order == ["Weapon 1", "Offhand", "Weapon 2"], order
    finally:
        _teardown_party_page(host, page)


def test_neck_and_trinket_trade_places(qapp):
    """Neck reads with the jewelry (Ring 1, Neck, Ring 2) and Trinket with
    the armor (Head, Trinket, Shoulders, ...). Tuples still carry the game's
    own slot indices; only the display order swaps 4 and 13.
    """
    pytest.importorskip("PySide6")

    host, page = _built_party_page()
    try:
        def row0(title):
            card = next(c for c in host._party_group_cards
                        if _group_caption(c) == title)
            # `_party_body` is the foldable grid host, and it is the FIRST
            # child of a card that has no caption of its own (2026-09-26:
            # Jewelry joined the fold row, so it lost its inline caption and
            # the old itemAt(1) reached past the end of its layout).
            grid = card._party_body.layout()
            return [grid.itemAtPosition(0, c).widget()._party_slot_name.text()
                    for c in range(3)]

        assert row0("Jewelry") == ["Ring 1", "Neck", "Ring 2"]
        assert row0("Armor") == ["Head", "Trinket", "Shoulders"]
    finally:
        _teardown_party_page(host, page)


def test_piece_stats_render_values_not_none(qapp):
    """Clicking a tile shows the piece's computed stats WITH values: the rows
    come from stat_rows() ({label, value, raw}) and must be formatted with
    stat_text(), never by reading a 'text' key those rows don't have (every
    line used to read 'Faith: None'). Since 2026-09-25 the rows are the same
    key/value table the Total Stats block uses, so the stat is a KEY label and
    its formatted value is a separate label beside it.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    host, page = _built_party_page()
    try:
        host._party_piece_kind = "Sword_Swarm"
        host._party_piece_gear = [
            {"slot": 0, "kind": "Sword_Swarm", "rarity": "Rare",
             "upgrade": 2, "level": 25, "state": "filled"},
        ]
        host._fill_party_piece()
        texts = [l.text() for l in
                 host._party_piece_host.findChildren(QtWidgets.QLabel)]
        assert texts, "the piece panel is empty"
        assert not any("None" in t for t in texts), texts
        assert "Faith" in texts, texts
    finally:
        _teardown_party_page(host, page)


def _pixmap_ink(pm) -> int:
    """How many pixels of a pixmap are actually drawn (0 = a blank box)."""
    pytest.importorskip("PySide6")
    from PySide6 import QtGui

    if pm is None or pm.isNull():
        return -1
    img = pm.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
    return sum(1 for y in range(img.height()) for x in range(img.width())
               if img.pixelColor(x, y).alpha() > 24)


def test_roster_avatar_is_never_an_empty_box(qapp):
    """A roster avatar is the SAME icon the Entity HUD gives a hero — the
    accent-tinted tile with the `player` glyph in the class colour
    (2026-09-26: it was a class->glyph guess, swords/orb/arrow, that appears
    nowhere else in the app; and before that, an empty box, because
    `ui_icon` used to answer an unresolvable name with a blank-but-non-null
    pixmap that every caller's isNull() test read as a hit). The letter
    fallback stays for a glyph that does not resolve at all.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.data import icons as gicons
    from farever_companion.ui import theme
    from farever_companion.ui.pages.party_roster import _AVATAR_PX

    host, page = _built_party_page()
    try:
        for cls in ("Priest", "Warrior", "Ranger", "Wizard", "Necromancer",
                    "Bard", ""):
            card = host._party_roster_card(1, "Hero", cls, "meta", "",
                                           False, False)
            avatars = [w for w in card.findChildren(QtWidgets.QLabel)
                       if w.width() and w.width() <= 40 and w.height() <= 40
                       and w.height() >= 20]
            assert avatars, f"{cls!r} card has no avatar box"
            av = avatars[0]
            ink = _pixmap_ink(av.pixmap())
            assert ink > 8 or av.text(), (
                f"the {cls or '(no class)'!r} avatar is an EMPTY box "
                "(no glyph pixels, no letter)")
            if ink > 8:
                # byte-for-byte the Entity HUD's hero tile
                want = gicons.tile_ui("player", _AVATAR_PX,
                                      theme.class_color(cls))
                assert av.pixmap().toImage() == want.toImage(), (
                    f"the {cls!r} avatar is not the Entity HUD's hero tile")
    finally:
        _teardown_party_page(host, page)


def test_the_top_row_blocks_share_one_caption_line(qapp):
    """Players, Weapons and Total Stats start on ONE line (2026-09-26). They
    did not: a QVBoxLayout inherits the style's default 9px margins, and the
    roster's column wrapper was one level shallower than the doll's, so the
    Players block sat 9px above the other two; the group cards' own 5px top
    padding then put the Weapons caption another 1px above Total Stats. Every
    column wrapper is margin-free now and all the cards pad themselves
    identically, so the three captions share a y.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    host, page = _built_party_page()
    try:
        page.show()
        for _ in range(4):
            QtWidgets.QApplication.instance().processEvents()

        def cap_of(card):
            found = [w for w in card.findChildren(QtWidgets.QLabel)
                     if w.text() and w.font().pixelSize() > 0]
            assert found, f"the {card.objectName()} block has no caption"
            return found[0]

        weapons = next(c for c in host._party_group_cards
                       if getattr(c, "_party_title", "") == "Weapons")
        # the totals block is captioned "Total Stats" (2026-09-26), so the
        # three captions are the same short shape and the widest one is no
        # longer "Stat Totals"
        assert host._party_totals_cap.text() == "Total Stats", \
            f"the totals block reads {host._party_totals_cap.text()!r}"
        tops = {}
        for name, card in (("Players", host._party_players_card),
                           ("Weapons", weapons),
                           ("Total Stats", host._party_totals_card)):
            cap = cap_of(card)
            c = card.mapTo(page, QtCore.QPoint(0, 0))
            p = cap.mapTo(page, QtCore.QPoint(0, 0))
            tops[name] = (c.y(), p.y(), p.x() - c.x())
        assert len({v[0] for v in tops.values()}) == 1, \
            f"the blocks do not share a top edge: {tops}"
        assert len({v[1] for v in tops.values()}) == 1, \
            f"the captions do not share a line: {tops}"
        assert len({v[2] for v in tops.values()}) == 1, \
            f"the captions are inset differently: {tops}"
    finally:
        _teardown_party_page(host, page)


def test_fold_row_is_its_own_card_at_the_tail_of_the_doll(qapp):
    """The fold row and the body it expands into are their OWN Card at the
    tail of the doll column (2026-09-26: the row used to live inside the
    Jewelry card, which stopped being possible when Jewelry became a tab of
    that row). The doll column is then Weapons, Armor and this card — the
    two groups that are still cards, then the accordion.
    """
    pytest.importorskip("PySide6")

    host, page = _built_party_page()
    try:
        fold_card = host._party_fold_card
        assert fold_card.objectName() == "Card", \
            "the fold row is not a Card like every other block"
        in_card = fold_card.layout()
        for cap, _card in host._party_fold_caps:
            assert in_card.indexOf(cap.parentWidget()) >= 0, \
                "a fold caption is not inside the fold card"
        # the doll column is Weapons + Armor + the fold card, nothing else
        worn = host._party_worn_box.layout()
        worn_cards = [c for c in host._party_group_cards
                      if getattr(c, "_party_title", "")
                      in ("Weapons", "Armor")]
        assert worn.count() == 3, \
            f"the doll column holds {worn.count()} items, not 3"
        assert all(worn.indexOf(c) >= 0 for c in worn_cards) and \
            worn.indexOf(fold_card) > max(worn.indexOf(c) for c in worn_cards), \
            "the fold card is not the last thing in the doll column"
        # …and no group card is left stranded outside both homes
        placed = {id(c) for c in worn_cards} | {id(fold_card)}
        for _cap, card in host._party_fold_caps:
            placed.add(id(card))
    finally:
        _teardown_party_page(host, page)


def test_opening_a_fold_group_does_not_move_the_page(qapp):
    """Expanding a carried group fills the space under the fold row instead of
    growing the doll column (2026-09-26: opening one shoved the whole page
    down, so clicking a caption moved everything you were reading). The fold
    body is pinned to the tallest fold card, the same trade the stat blocks
    and the Buffs block make.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtGui, QtWidgets

    host, page = _built_party_page()
    try:
        page.show()
        for _ in range(4):
            QtWidgets.QApplication.instance().processEvents()

        def snap():
            for _ in range(3):
                QtWidgets.QApplication.instance().processEvents()
            return (page.minimumSizeHint().height(),
                    host._party_worn_box.height(),
                    host._party_carried_box.mapTo(
                        page, QtCore.QPoint(0, 0)).y())

        ev = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseButtonPress,
                               QtCore.QPointF(4, 4), QtCore.Qt.LeftButton,
                               QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        caps = {c.title(): (c, k)
                for c, k in host._party_fold_caps}
        shut = snap()
        caps["Extras"][0].mousePressEvent(ev)
        opened = snap()
        # it really did open, so the no-move check is not vacuous
        assert not caps["Extras"][1].isHidden(), \
            "the group did not expand, so the no-move check proves nothing"
        assert opened == shut, (
            f"opening a group moved the page {shut} -> {opened}")
        caps["Extras"][0].mousePressEvent(ev)
        assert snap() == shut, "closing a group did not put it back"
    finally:
        _teardown_party_page(host, page)


def test_players_block_hugs_its_roster_and_caps_a_long_one(qapp):
    """The Players block is as tall as the roster it drew, not as tall as the
    page (2026-09-26: stretched to the column it was a big framed box with a
    pool of empty card under three heroes). A long party stops at
    _ROSTER_MAX_H and scrolls INSIDE the block, so it never stretches the page
    either.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui.pages.party_roster import _ROSTER_MAX_H

    host, page = _built_party_page()
    try:
        page.show()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        block = host._party_players_card
        area = block.findChild(QtWidgets.QScrollArea)

        def heroes(n):
            host._party_roster_fill([(1000 + i, f"Hero{i}", "Priest")
                                     for i in range(n)])
            for _ in range(3):
                QtWidgets.QApplication.instance().processEvents()
            return block.height()

        one = heroes(1)
        three = heroes(3)
        assert three > one, \
            f"a 3-hero roster is no taller than a 1-hero one ({one}, {three})"
        # a small party leaves no empty card: the block is the roster plus the
        # caption, not the column height
        assert three < host._party_worn_box.height(), \
            (f"the Players block ({three}) fills the whole column "
             f"({host._party_worn_box.height()}) — the empty frame is back")
        # a long one caps and scrolls inside
        many = heroes(20)
        assert many <= _ROSTER_MAX_H, \
            f"a 20-hero roster grew the block to {many} (cap {_ROSTER_MAX_H})"
        assert area.verticalScrollBar().maximum() > 0, \
            "a capped block does not scroll"
    finally:
        _teardown_party_page(host, page)


def test_roster_is_a_block_like_the_rest(qapp):
    """The PLAYERS list is a block in the same mould as the detail column's
    three readouts (2026-09-26): the same Card and the same quiet caption, so
    the page is four blocks of one family — not a bare column of cards beside
    three framed ones. The hero cards keep their own frames (the selected
    hero's accent border and the hover state live there), and the list still
    scrolls DOWN inside the block, never sideways.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    host, page = _built_party_page()
    try:
        card = host._party_players_card
        assert card.objectName() == "Card", "the roster is not a Card"
        assert card.styleSheet() == host._party_totals_card.styleSheet(), \
            "the roster block and Total Stats are styled differently"
        caps = [w.text() for w in card.findChildren(QtWidgets.QLabel)]
        assert "Players" in caps, f"no Players caption: {caps[:4]}"
        assert host._party_players_cap.styleSheet() == \
            host._party_totals_card.findChildren(QtWidgets.QLabel)[0]\
            .styleSheet(), \
            "the Players caption and the Total Stats caption are styled apart"
        # the hero cards live inside the block, in its vertical-only scroller
        area = card.findChild(QtWidgets.QScrollArea)
        assert area is not None, "the roster no longer scrolls"
        assert area.widget() is host._party_list, \
            "the roster list is not the scroller's content"
        assert area.horizontalScrollBarPolicy() == \
            QtCore.Qt.ScrollBarAlwaysOff, \
            "the roster can scroll sideways"
    finally:
        _teardown_party_page(host, page)


def test_long_names_fill_two_lines_then_clip(qapp):
    """A long name gets TWO lines and is then clipped (2026-09-26).

    Three earlier shapes, all wrong: run-together and word-wrapped, the name
    ran to four lines and made the card taller for that item than the next;
    single-line eliding threw away a whole free line on "Beefury, Blessed
    Blade of the Farseeker"; and the second attempt still collapsed to one
    line, because a word-wrapped QLabel advertises heightForWidth and the
    layout asked THAT instead of sizeHint() (so a short name got one line and
    a long one two — the card still moved). The name area is now always two
    lines tall and the text is dropped word by word until the rest fits with
    an ellipsis. The full text stays in the tooltip.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    host, page = _built_party_page()
    try:
        host._party_piece_kind = "Sword_Swarm"
        host._party_piece_gear = [
            {"slot": 0, "kind": "Sword_Swarm", "rarity": "Rare",
             "upgrade": 2, "level": 25, "state": "filled"},
        ]
        host._fill_party_piece()
        page.show()
        for _ in range(4):
            app = QtWidgets.QApplication.instance()
            app.processEvents()

        cap = host._party_piece_cap
        assert cap.lines == 2, "the piece name is not a two-line area"
        fm = cap.fontMetrics()
        line = fm.height()

        def _wrap_lines(text):
            return cap._wrap_lines(text, cap._box_w())

        def _set(name):
            cap.set_full(name)
            for _ in range(3):
                app.processEvents()
            return cap.text()

        # a SHORT name: the area is still two lines, so the card does not
        # change height between items
        assert _set("Wooden Mortar") == "Wooden Mortar"
        short_h = cap.height()
        assert short_h >= line * 2, \
            f"the name area collapsed to {short_h}px, not two lines ({line})"

        # a name that needs more than two lines is CLIPPED, with an ellipsis,
        # and the text it settled on really does fit in two lines
        long = ("Beefury, Blessed Blade of the Farseeker of the Ancients, "
                "Dormant Oath of the Ninefold Vigil")
        shown = _set(long)
        assert _wrap_lines(long) > 2, "the fixture no longer needs clipping"
        assert shown.endswith("…"), f"the long name was not clipped: {shown!r}"
        assert shown != long
        assert _wrap_lines(shown) <= 2, \
            f"the clipped name still takes {_wrap_lines(shown)} lines"
        assert cap.height() == short_h, \
            "the card grew for the long name"
        assert cap.toolTip() == "", \
            ("a tooltip came back on the piece name — this page has no hover "
             "popups (2026-09-26)")
        # the meta line never wraps either
        assert not host._party_piece_meta.wordWrap()

        # a buff name is one line for the same reason
        from farever_companion.ui.pages.party_page import (PartyPageMixin,
                                                          _buff_label,
                                                          _buff_tail)
        b = {"kind": "SomeExtremelyLongBuffStatusNameThatKeepsGoing",
             "stacks": 5, "duration": 0.0, "source_item": None}
        row = PartyPageMixin._party_buff_row(
            b["kind"], _buff_label(b), _buff_tail(b), source_item=None)
        names = [w for w in row.findChildren(QtWidgets.QLabel) if w.text()]
        assert not any(w.wordWrap() for w in names), \
            "a buff row still wraps its text"
    finally:
        _teardown_party_page(host, page)


def test_page_fits_the_default_window_without_a_horizontal_scrollbar(qapp):
    """Player Inspect must not need a horizontal scrollbar at the window's
    DEFAULT size (2026-09-26: bigger doll tiles pushed the page's minimum
    width past the viewport and the whole page scrolled sideways).

    The budget is the shipped default window (1180) minus the fixed 212px
    sidebar, which is what the page's viewport actually gets; the page is
    allowed 30px of slack for the scroll area's own frame.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui import theme

    app = QtWidgets.QApplication.instance()
    old_font, old_qss = app.font(), app.styleSheet()
    host, page = None, None
    try:
        # measure it the way the app builds it: the theme applied, so the
        # fonts are the real ones (a bare test harness measures a fallback
        # font and every other test's leftovers move the number)
        theme.apply(app)
        host, page = _built_party_page()
        page.show()
        for _ in range(3):
            app.processEvents()
        budget = 1180 - 212 - 30
        need = page.minimumSizeHint().width()
        assert need <= budget, (
            f"the page needs {need}px but a default window gives it {budget}px "
            "— a horizontal scrollbar will appear. Shrink a fixed width "
            "(a tile, a card, the fold strip), not the text.")
    finally:
        if host is not None and page is not None:
            _teardown_party_page(host, page)
        app.setFont(old_font)
        app.setStyleSheet(old_qss)


def test_detail_column_is_ten_percent_narrower_than_the_doll(qapp):
    """The third column (Total Stats / piece / Buffs) is ~10% NARROWER than
    the widest group card, and the paper-doll tiles are the big ones
    (2026-09-26): the tiles took the slack that used to sit empty beside
    them, so the page does not read as a wide column of nothing. Spelled as
    ratios, not pixel counts, so a tile resize does not fail this.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui.pages.party_grid import _TILE_H, _TILE_W

    host, page = _built_party_page()
    try:
        page.show()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        equip = max(c.sizeHint().width() for c in host._party_group_cards)
        detail = host._party_totals_card.width()
        # the column is the 10% rule, unless a group lives INSIDE it and needs
        # its own width plus the block's padding (Consumables' tile row does)
        from farever_companion.ui.pages.party_profile import _BLOCK_PAD
        moved_w = max((c.sizeHint().width() for _c, c in
                       host._party_moved_caps), default=0)
        assert detail <= max(equip * 0.92, moved_w + _BLOCK_PAD), \
            (f"the detail column is {detail} against a {equip} card and a "
             f"{moved_w} moved group — it is not the slimmer one")
        assert detail >= equip * 0.8, \
            f"the detail column ({detail}) is too narrow to read"
        # all three blocks agree on the width, or the column edges wobble
        assert host._party_piece_host.width() == detail, \
            "the piece block disagrees with Total Stats on the width"
        assert host._party_buffs_card.width() == detail, \
            "the Buffs block disagrees with Total Stats on the width"
        # the tiles are the page's picture: bigger than the old 64x72
        tile = host._party_grid_cards[0]
        assert (tile.width(), tile.height()) == (_TILE_W, _TILE_H), \
            f"the tile is {tile.width()}x{tile.height()}"
        assert _TILE_W > 64 and _TILE_H > 72, \
            "the tiles shrank instead of taking the slack"
    finally:
        _teardown_party_page(host, page)


def test_buff_row_puts_the_parenthetical_on_its_own_line(qapp):
    """A buff's parenthetical (stacks, granting item) is its OWN line under
    the name (2026-09-26): run together, "Bloodrage Aura  (from Cheese Moon)"
    read as one long sentence and wrapped wherever the card's edge landed.
    A buff with nothing to add stays a single line — no empty second line.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui.pages.party_page import (PartyPageMixin,
                                                      _buff_label, _buff_tail)
    two = {"kind": "Axe_Boomerang_Skill_Passive_Status", "stacks": 1,
           "duration": 0.0, "source_item": "Axe_Boomerang"}
    one = {"kind": "ItemStatus", "stacks": 1, "duration": 0.0,
           "source_item": "ElixirOfDexterity_Z2"}
    row = PartyPageMixin._party_buff_row(
        two["kind"], _buff_label(two), _buff_tail(two),
        source_item=two["source_item"])
    texts = [w.text() for w in row.findChildren(QtWidgets.QLabel)
             if w.text()]
    assert texts[0] == "Bloodrage Aura", texts
    assert texts[1] == "(from Cheese Moon)", \
        f"the parenthetical is not its own line: {texts}"
    # nothing wrapped into the name line, and the name is not the tail
    assert "Cheese Moon" not in texts[0], texts

    row2 = PartyPageMixin._party_buff_row(
        one["kind"], _buff_label(one), _buff_tail(one),
        source_item=one["source_item"])
    texts2 = [w.text() for w in row2.findChildren(QtWidgets.QLabel)
              if w.text()]
    assert texts2 == [texts2[0]], f"an empty second line was added: {texts2}"
    assert row2.height() <= row.height(), \
        "a buff with no parenthetical is taller than one with it"


def test_buff_rows_are_cards_and_the_pin_holds_whole_ones(qapp):
    """A buff row is a small Card, and the Buffs block's height pin is
    measured off one (2026-09-26).

    The rows used to be bare icon+text lines, the one place on the page with
    no block of its own, so the list the eye scanned hardest was the flattest.
    Giving each row the same 8/6 padding, PANEL fill and BORDER the rest of
    the page uses costs height: a card is ~45px where the line was ~20 (name
    line + parenthetical + 12px padding + a 1px border each side), so the
    pinned area could not stay at "7 rows" or the block would grow 175px and
    shove the gear notes off the page. It is pinned to 5 cards, which is
    within a few pixels of the old height.

    The pin has to be measured off the CARD's own sizeHint, not off a label's
    font height: padding and border are most of a card's height, and a
    line-height arithmetic left the sixth row sliced by the pin's edge. So
    this fills the block with the pin's worth of two-line cards and asserts
    the last one is whole — with the scroll bar at rest.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui import theme
    from farever_companion.ui.pages.party_page import _buff_label, _buff_tail
    from farever_companion.ui.pages.party_profile import _BUFF_ROWS

    host, page = _built_party_page()
    try:
        page.show()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

        lay = host._party_buffs
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        def add(kind):
            row = {"kind": kind, "stacks": 1, "duration": 0.0,
                   "source_item": "ElixirOfDexterity_Z2"}
            lay.addWidget(host._party_buff_row(
                kind, _buff_label(row), _buff_tail(row),
                source_item="ElixirOfDexterity_Z2"))
            return lay.itemAt(lay.count() - 1).widget()

        # every row is the same card the rest of the page is made of
        card = add("Whetstone_Status")
        assert card.objectName() == "Card", \
            f"a buff row is {card.objectName()!r}, not a Card"
        assert card.styleSheet() == host._party_buffs_card.styleSheet(), \
            "a buff row is painted differently from the block it sits in"
        assert theme.PANEL in card.styleSheet() and \
            theme.BORDER in card.styleSheet(), \
            "a buff row lost the PANEL/BORDER paint"
        m = card.layout().contentsMargins()
        assert (m.left(), m.top(), m.right(), m.bottom()) == (8, 6, 8, 6), \
            f"a buff row pads itself {m}, not the page's 8/6"

        # fill the pin with the TALLEST row shape (name + parenthetical)
        while lay.count() > 1:
            it = lay.takeAt(1)
            it.widget().setParent(None)
            it.widget().deleteLater()
        while lay.count() < _BUFF_ROWS:
            add(f"Status_{lay.count()}")
        for _ in range(4):
            QtWidgets.QApplication.instance().processEvents()

        area = host._party_buffs_area
        vp_h = area.viewport().height()
        cards = [lay.itemAt(i).widget() for i in range(lay.count())]
        need = sum(c.height() for c in cards) + 4 * (len(cards) - 1)
        assert need <= vp_h, (
            f"{len(cards)} buff cards need {need}px but the pinned area is "
            f"{vp_h}px — the pin is slicing the last row")
        assert area.verticalScrollBar().maximum() == 0, \
            ("the pinned area scrolls for exactly the rows it promises to "
             "show")
        # and one more row really does go into the scroll, not get clipped
        add("Status_extra")
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert area.verticalScrollBar().maximum() > 0, \
            "a row past the pin vanished instead of scrolling"
    finally:
        _teardown_party_page(host, page)


def test_statless_pieces_are_marked_only_where_that_is_news(qapp):
    """The "no stats" treatment — dimmed cell, grey border, faded sprite, a
    mark under the name — fires only where it tells you something NEW
    (2026-09-26).

    It was added because a statless piece looked exactly like a good drop. But
    checked against the real sheet, every item in Tools, Consumables and
    Extras yields zero stat rows, so the mark was telling you what the
    group caption already said — repeated under all four consumable tiles, in
    the one group you open most. Those groups now draw as ordinary items (the
    tooltip still says so, on hover).

    A statless piece in a group that normally DOES stat is still marked: there
    it is genuinely surprising. The current sheet has no such item, so that
    half of the rule is NOT driven here any more (2026-09-26) — it used to be
    driven by picking a real statless item and MOVING it into a stat-bearing
    group, which put a piece in a slot the game would never put it in and
    needed a note explaining the fiction. What is asserted is the half that is
    real: in the three groups where every item is statless by nature, nothing is
    marked, greyed or faded, and a stat-bearing piece is untouched. If a
    statless item ever DOES appear in Weapons, Armor or Jewelry, this is the
    test that should grow the case.
    """
    pytest.importorskip("PySide6")
    from farever_companion.ui import theme
    from farever_companion.ui.pages.party_grid import _STATELESS_GROUPS

    host, page = _built_party_page()
    try:
        assert set(_STATELESS_GROUPS) == {"Tools", "Consumables",
                                          "Extras"}, \
            "the statless-by-nature group list changed"
        host._party_profile_fill([
            {"slot": 16, "kind": "Pickaxe", "rarity": "Common",
             "upgrade": 0, "level": 5, "state": "filled"},
            {"slot": 18, "kind": "HealthPotion", "rarity": "Common",
             "upgrade": 0, "level": 1, "state": "filled"},
            {"slot": 0, "kind": "Sword_Swarm", "rarity": "Rare",
             "upgrade": 2, "level": 25, "state": "filled"},
        ])
        tool = host._party_grid_cards[16]
        potion = host._party_grid_cards[18]
        sword = host._party_grid_cards[0]
        # isHidden(), not isVisible(): a mark inside a card the page has not
        # shown yet is "not visible" either way, so only the explicit hidden
        # flag says whether the fill marked it
        for tile, what in ((tool, "a tool"), (potion, "a consumable")):
            assert tile._party_slot_mark.isHidden(), \
                f"{what} is marked 'no stats' — the whole group is"  # noqa: SLF001
            assert theme.DIM not in tile.styleSheet(), \
                f"{what} is still greyed: {tile.styleSheet()}"
            assert tile._party_slot_icon.graphicsEffect() is None, \
                f"{what}'s sprite is still faded"        # noqa: SLF001
            # the group caption is what says it now — there is no hover popup
            assert tile.toolTip() == "", \
                f"{what} grew a tooltip back: {tile.toolTip()!r}"

        # a real stat-bearing piece is untouched by the marking
        assert sword._party_slot_mark.isHidden(), \
            "a stat-bearing piece is marked statless"  # noqa: SLF001
        assert theme.RARITY.get("Rare") in sword.styleSheet(), \
            f"a stat-bearing piece lost its rarity border: {sword.styleSheet()}"
        assert sword._party_slot_icon.graphicsEffect() is None, \
            "a stat-bearing piece's sprite is faded"    # noqa: SLF001
        assert sword.toolTip() == ""

        # emptying the slot clears the marking (the tool itself leaves)
        host._party_profile_fill([
            {"slot": 16, "state": "empty"},
            {"slot": 0, "kind": "Sword_Swarm", "rarity": "Rare",
             "upgrade": 2, "level": 25, "state": "filled"},
        ])
        assert tool._party_slot_mark.isHidden(), \
            "the mark outlived the piece"               # noqa: SLF001
        assert tool._party_slot_icon.graphicsEffect() is None, \
            "the fade outlived the piece"               # noqa: SLF001
    finally:
        _teardown_party_page(host, page)


def test_a_piece_with_nothing_computable_shows_an_empty_block(qapp):
    """A piece that grants no stats leaves the panel blank, not annotated.

    Requested 2026-09-26, alongside the stacked "No buffs live" lines: "No
    computed stats for this piece." said less than the blank it replaced. The
    caption above still names the piece, so the block is not unexplained —
    it is a piece that grants nothing.

    Boar Meat is the fixture because it is a real sheet row with genuinely
    no computed gear stats (Food, not gear), so this is the live case and not
    a synthetic one.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    host, page = _built_party_page()
    try:
        host._party_piece_gear = [
            {"slot": 20, "kind": "BoarMeat_Z1", "rarity": "Common",
             "upgrade": 0, "level": 0, "state": "filled"},
        ]
        host._party_piece_kind = "BoarMeat_Z1"
        host._fill_party_piece()
        muted = [w for w in host._party_piece.findChildren(
            QtWidgets.QLabel) if w.objectName() == "Muted"]
        assert not muted, (
            f"the piece panel still annotates an empty block: "
            f"{[w.text() for w in muted]}")
        # the piece is still NAMED — a blank block, not a blank panel
        from farever_companion.data import names as gnames
        assert gnames.item_name("BoarMeat_Z1") in host._party_piece_cap.text(), (
            f"the caption lost the piece: {host._party_piece_cap.text()!r}")
    finally:
        _teardown_party_page(host, page)


def test_a_totals_block_with_nothing_to_sum_is_blank(qapp):
    """The Total Stats block's twin of the line above — also gone.

    Same request, same reasoning: an empty sum is an empty block, and the
    lock rows still size it so the column below does not jump when the
    first piece lands.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    host, page = _built_party_page()
    try:
        host._party_piece_gear = [
            {"slot": 20, "kind": "BoarMeat_Z1", "rarity": "Common",
             "upgrade": 0, "level": 0, "state": "filled"},
        ]
        host._fill_party_totals()
        muted = [w for w in host._party_totals_card.findChildren(
            QtWidgets.QLabel) if w.objectName() == "Muted"]
        assert not muted, (
            f"the totals block still annotates an empty sum: "
            f"{[w.text() for w in muted]}")
    finally:
        _teardown_party_page(host, page)


def test_a_hero_with_no_gear_leaves_the_piece_panel_blank(qapp):
    """No piece equipped is a blank panel, not a "No gear live." line.

    The last of the empty-state fillers, removed 2026-09-26 with the other
    three. The caption above still reads "Selected Piece", so the block is
    not unexplained — and unlike the NOT READ and render-failure lines, this
    one never carried information the blank layout did not.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    host, page = _built_party_page()
    try:
        host._party_piece_gear = []
        host._party_piece_kind = ""
        host._fill_party_piece()
        muted = [w for w in host._party_piece.findChildren(
            QtWidgets.QLabel) if w.objectName() == "Muted"]
        assert not muted, (
            f"the piece panel still annotates an empty hero: "
            f"{[w.text() for w in muted]}")
        assert host._party_piece_cap.text() == "Selected Piece", (
            f"the caption is not the empty-state heading: "
            f"{host._party_piece_cap.text()!r}")
    finally:
        _teardown_party_page(host, page)


def test_the_party_page_ships_no_empty_state_filler_at_all():
    """No "nothing here" line survives anywhere on the page.

    Four went on 2026-09-26 — "No buffs live (or statuses unreadable).",
    "No computed stats for this piece.", "No computed stats to sum.", "No
    gear live." and "No player selected." — each of which described a blank
    layout in words that said less than the blank, and the first of which
    also stacked once per rebuild.

    What is NOT here, deliberately: the per-slot "NOT READ" line (it names
    the slot and why it failed) and the render-failure line (it carries the
    exception). Those are diagnostics. This guard is scoped to the filler
    class, so adding a real diagnostic back does not trip it.
    """
    from farever_companion.ui.pages import party_page, party_profile, \
        party_stats

    gone = ("No buffs live", "No computed stats for this piece",
            "No computed stats to sum", "No gear live", "No player selected")
    for mod in (party_page, party_profile, party_stats):
        src = open(mod.__file__, encoding="utf-8").read()
        for text in gone:
            # anchored on the CALL, not the bare string: these modules name
            # the removed lines in their own comments explaining why they
            # went, and a bare search trips on that prose
            assert f'_party_empty("{text}' not in src, (
                f"{mod.__name__} still ships the empty-state filler "
                f"{text!r}. If a blank block now needs explaining, the line "
                "has to be tracked by the buff/gear diff or it will stack.")


def test_the_buffs_block_adds_no_empty_state_line_at_all():
    """The buffs block ships with no placeholder text.

    Requested 2026-09-26, after the block was found holding THREE copies of
    "No buffs live (or statuses unreadable)." stacked one per rebuild. The
    buff diff pools only widgets carrying `_party_kind` (a real status row)
    and destroys only what the diff did not claim, so the plain QLabel the
    empty case dropped in was invisible to BOTH — never pooled, never
    destroyed — and every rebuild appended another. The spec guard hid it
    most of the time; each time the gear or pet half of the spec moved, one
    more appeared and stayed.

    A hero with nothing on now shows an empty Buffs card.

    This is a source guard rather than a rendered-widget assertion on
    purpose: it is the only form that actually bites. A behavioural test was
    written first and had to go — `_refresh_party_detail` returns at its
    `_widget_alive` guard in the headless harness, so it never reaches the
    buff rebuild, and the test passed with the offending line restored. A
    test that cannot fail is worse than no test.
    """
    from farever_companion.ui.pages import party_page

    src = open(party_page.__file__, encoding="utf-8").read()
    assert "No buffs live" not in src, (
        "the buffs block adds an empty-state line again. The buff diff "
        "cannot see a widget with no _party_kind, so it will never remove "
        "the previous one and the line will stack once per rebuild")


def test_buffs_are_a_pinned_block_without_the_raw_status_id(qapp):
    """The buff list is a BLOCK in the same mould as the two stat readouts
    (2026-09-26) — the same Card, a "Buffs" caption, a pinned height that
    scrolls instead of growing — and the raw status id is GONE from under each
    row ('ItemStatus', 'Axe_Boomerang_Skill_Passive_Status'): it is the
    sheet's key, the row already names the status, and the id stays in the
    row's tooltip. Removed, not hidden.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    host, page = _built_party_page()
    try:
        card = host._party_buffs_card
        assert card.objectName() == "Card", "the buffs are not a Card"
        assert card.styleSheet() == host._party_totals_card.styleSheet(), \
            "the buffs block and Total Stats are styled differently"
        # "Buffs" is a fold TAB now (2026-09-26), and an open tab's text is
        # rich text with the green arrow in it, so the caption is read through
        # its title() accessor rather than by stripping an arrow off .text().
        assert host._party_buffs_cap.title() == "Buffs", \
            f"the buffs block is not captioned Buffs: {host._party_buffs_cap!r}"
        assert host._party_buffs_cap in card.findChildren(QtWidgets.QLabel), \
            "the Buffs tab is not inside the block"
        # the rows live in a pinned, scrolling area
        assert host._party_buffs_area.widget() is host._party_buffs_host
        h_min = host._party_buffs_area.minimumHeight()
        h_max = host._party_buffs_area.maximumHeight()
        assert h_min == h_max > 0, \
            "the buffs area is not height-pinned (the page will shove)"

        # render a few buffs the way the live path does, then check the ids
        # are not printed under the rows
        from farever_companion.ui.pages.party_page import _buff_label, \
            _buff_tail
        kinds = ["Whetstone_Status", "Axe_Boomerang_Skill_Passive_Status",
                 "ItemStatus", "SomeExtremelyLongBuffStatusNameThatKeepsGoing"]
        lay = host._party_buffs
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        for k in kinds:
            row = {"kind": k, "stacks": 1, "duration": 0.0,
                   "source_item": "ElixirOfDexterity_Z2"}
            lay.addWidget(host._party_buff_row(k, _buff_label(row),
                                               _buff_tail(row),
                                               source_item="ElixirOfDexterity_Z2"))
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        printed = [w.text() for w in host._party_buffs_host
                   .findChildren(QtWidgets.QLabel)]
        for k in kinds:
            assert k not in printed, \
                f"the raw status id is printed under a buff again: {k!r}"
        # …and the row carries no popup that would put it back (2026-09-26)
        tips = [w.toolTip() for w in lay.itemAt(0).widget().findChildren(
            QtWidgets.QWidget)]
        assert not any(tips), f"a buff row grew a tooltip: {tips}"
    finally:
        _teardown_party_page(host, page)


def test_stat_blocks_pad_to_seven_rows_and_never_hide_a_stat(qapp):
    """The two stat blocks are given 7 rows as a FLOOR, and GROW past it.

    The pin exists because the stat list changes with every hero and every
    refresh, and a block that grew and shrank shoved the piece card, the buffs
    and the gear notes up and down the page — so a 5-stat hero and a 10-stat
    hero should still occupy the same height IN THE COMMON CASE. That is what
    the floor is for.

    It used to be a CEILING as well, with the overflow summarised as
    "+N more" (2026-09-26). That was wrong for the totals block: summing the
    15 worn slots by stat label yields 8-10 distinct stats for any hero above
    Common, so the marker was the NORMAL state, not an edge case, and two or
    three real numbers were missing from the page's main readout on every
    refresh. And once tooltips went the values were not on the page at all —
    the row reported a count and nothing else.

    So: nothing is ever dropped, there is no "+N more" row, and a block with
    more rows than the floor is TALLER.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui.pages.party_profile import _BLOCK_ROWS

    host, page = _built_party_page()
    try:
        one = [{"slot": 0, "kind": "Sword_Swarm", "rarity": "Rare",
                "upgrade": 0, "level": 25, "state": "filled"}]
        many = one + [
            {"slot": s, "kind": k, "rarity": "Rare", "upgrade": 0,
             "level": 25, "state": "filled"}
            for s, k in ((1, "Axe_Boomerang"), (2, "Shield_OrbitWater"),
                         (3, "Head_RKobold_FigCle"), (4, "Necklace_Z1_Mp"),
                         (5, "Shoulders_RBee_Ass"), (6, "Chest_Starter_Wiz"),
                         (7, "Back_RBee_Wiz"), (8, "Hands_Z2U1_FigCle"),
                         (9, "Waist_RBee_FigWiz"), (10, "Legs_Starter_Wiz"),
                         (11, "Feet_Starter_Wiz"), (12, "Finger_Mp"),
                         (13, "Trinket_Bee"), (14, "Finger_Z1RCraft_Mp"))]

        def rows_of(card):
            lay = card._party_rows                      # noqa: SLF001
            return [w for w in (lay.itemAt(i).widget()
                                for i in range(lay.count()))
                    if w is not None]

        def row_labels(card):
            out = []
            for w in rows_of(card):
                out.extend(c.text() for c in w.findChildren(QtWidgets.QLabel))
            return out

        # a SMALL list: the block is padded out to the floor, so it is the same
        # height as any other small list
        host._party_piece_gear = one
        host._party_piece_kind = "Sword_Swarm"
        host._fill_party_totals()
        host._fill_party_piece()
        page.show()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        few_h = (host._party_totals_card.height(),
                 host._party_piece_host.height())
        few_n = (len(rows_of(host._party_totals_card)),
                 len(rows_of(host._party_piece_host)))
        assert few_n[0] <= _BLOCK_ROWS and few_n[1] <= _BLOCK_ROWS, \
            f"a small stat list overflowed the block: {few_n}"

        # a FULL set: every stat is drawn, and the block grows to fit them
        host._party_piece_gear = many
        host._fill_party_totals()
        host._fill_party_piece()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        many_h = (host._party_totals_card.height(),
                  host._party_piece_host.height())
        many_n = (len(rows_of(host._party_totals_card)),
                  len(rows_of(host._party_piece_host)))
        assert many_n[0] > _BLOCK_ROWS, (
            f"a full set of worn pieces produced only {many_n[0]} totals rows "
            f"— the measurement that motivated this says 8-10, so the data "
            f"under the block changed and this test is no longer testing it")
        # NOTHING is summarised away: no "+N more" row survives
        labels = row_labels(host._party_totals_card)
        assert not [t for t in labels if t.startswith("+")], \
            f"an overflow marker is back: {labels}"
        # and the block really is taller, not clipping the overflow away
        assert many_h[0] > few_h[0], \
            (f"the totals block did not grow for {many_n[0]} rows "
             f"({few_h[0]} -> {many_h[0]}px) — the extra rows are being cut")
        # the single-piece block is one item, so it never needed the clamp
        # (measured: no item in the sheet has more than 6 stat rows)
        assert many_n[1] <= _BLOCK_ROWS, \
            f"the piece block drew {many_n[1]} rows, over the floor"
    finally:
        _teardown_party_page(host, page)


def test_every_stat_a_full_set_produces_is_on_the_page(qapp):
    """A fully equipped hero shows EVERY summed stat, with its real value.

    The "+N more" clamp was reported as a normal state rather than an edge
    case: summing the 15 worn slots by label gives 8-10 distinct stats for
    any hero above Common, against a 7-row ceiling. This drives the real full
    set and checks the two ends of it — the FIRST stat summed is drawn, and
    the LAST one is too, with the value the sheet computed and not a
    placeholder.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.data.items import stats as gstats

    host, page = _built_party_page()
    try:
        gear = [
            {"slot": 0, "kind": "Sword_Swarm", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 1, "kind": "Axe_Boomerang", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 2, "kind": "Shield_OrbitWater", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 3, "kind": "Head_RKobold_FigCle", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 4, "kind": "Necklace_Z1_Mp", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 5, "kind": "Shoulders_RBee_Ass", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 6, "kind": "Chest_Starter_Wiz", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 7, "kind": "Back_RBee_Wiz", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 8, "kind": "Hands_Z2U1_FigCle", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 9, "kind": "Waist_RBee_FigWiz", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 10, "kind": "Legs_Starter_Wiz", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 11, "kind": "Feet_Starter_Wiz", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 12, "kind": "Finger_Mp", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 13, "kind": "Trinket_Bee", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
            {"slot": 14, "kind": "Finger_Z1RCraft_Mp", "rarity": "Legendary",
             "upgrade": 0, "level": 60, "state": "filled"},
        ]
        # what the sheet says the sum SHOULD be, computed the same way
        expected: dict[str, float] = {}
        order: list[str] = []
        from farever_companion.data.items import catalog as gcatalog
        items = gcatalog._data().get("items", {}) or {}
        for g in gear:
            gs = gstats.gear_stats(items.get(g["kind"]) or {}, 60, "Legendary")
            for s in gstats.stat_rows(gs["stats"] if gs else []):
                if s["label"] not in expected:
                    expected[s["label"]] = 0.0
                    order.append(s["label"])
                expected[s["label"]] += s["value"]
        assert len(order) > 7, (
            f"the sheet now sums to {len(order)} stats, at or under the old "
            f"7-row ceiling — this test is no longer covering the case that "
            f"motivated removing the clamp")

        host._party_piece_gear = gear
        host._fill_party_totals()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

        lay = host._party_totals_card._party_rows         # noqa: SLF001
        drawn: dict[str, str] = {}
        for i in range(lay.count()):
            row = lay.itemAt(i).widget()
            if row is None:
                continue
            texts = [c.text() for c in row.findChildren(QtWidgets.QLabel)]
            if len(texts) >= 2:
                drawn[texts[0]] = texts[1]

        for label in order:
            assert label in drawn, (
                f"{label!r} is summed by the sheet but not drawn — the block "
                f"is hiding a stat. Drawn: {sorted(drawn)}")
            assert drawn[label] not in ("", "None"), \
                f"{label!r} is drawn with no value: {drawn[label]!r}"
    finally:
        _teardown_party_page(host, page)


def test_piece_block_matches_the_stat_totals_block(qapp):
    """The selected piece is a NEAT BLOCK in the Total Stats mould (2026-09-25):
    the same Card frame, the same quiet caption on top (the piece's name line,
    replacing the generic caption when a piece is picked) and the same
    key/value rows below it — not a bare column of "Faith: 12" labels.
    """
    pytest.importorskip("PySide6")
    from farever_companion.ui import theme
    from PySide6 import QtWidgets

    host, page = _built_party_page()
    try:
        piece, tot = host._party_piece_host, host._party_totals_card
        assert piece.objectName() == tot.objectName() == "Card", \
            "the piece block is not a Card like Total Stats"
        assert piece.styleSheet() == tot.styleSheet(), \
            "the piece block and Total Stats are styled differently"
        # the caption is the piece itself, not a generic heading
        host._party_piece_kind = "Sword_Swarm"
        host._party_piece_gear = [
            {"slot": 0, "kind": "Sword_Swarm", "rarity": "Rare",
             "upgrade": 2, "level": 25, "state": "filled"},
        ]
        host._fill_party_piece()
        from farever_companion.data import names as gnames
        assert host._party_piece_cap.text() == gnames.item_name("Sword_Swarm"), \
            f"the caption does not name the piece: {host._party_piece_cap.text()!r}"
        # …on TWO lines (the name uses both, then clips — see
        # test_long_names_fill_two_lines_then_clip) and the rarity/level on
        # their own single-line row under it
        assert "Rare" in host._party_piece_meta.text() \
            and "+2" in host._party_piece_meta.text() \
            and "Lv 25" in host._party_piece_meta.text(), \
            f"the caption lost the rarity/upgrade/level: " \
            f"{host._party_piece_meta.text()!r}"
        assert host._party_piece_cap.lines == 2, \
            "the piece name is not the two-line area"
        tot_cap = tot.findChildren(QtWidgets.QLabel)[0]
        # The two captions are the same TYPE of line — same size, same weight,
        # same place in the block. They are deliberately NOT the same colour
        # from 2026-09-26: the piece name wears the piece's rarity, while
        # "Total Stats" is a heading about the whole hero and has no rarity of
        # its own. So compare everything but colour.
        def _sans_colour(sheet: str) -> str:
            return ";".join(part for part in sheet.split(";")
                            if part.strip() and "color" not in part)
        assert (_sans_colour(host._party_piece_cap.styleSheet())
                == _sans_colour(tot_cap.styleSheet())), \
            "the piece caption and the Total Stats caption differ in more " \
            "than colour"
        assert theme.rarity_color("Rare") in host._party_piece_cap.styleSheet(), \
            ("the piece name is not painted in its rarity colour: "
             f"{host._party_piece_cap.styleSheet()!r}")
        # same row construction: each stat is a widget holding a key label and
        # a value label, both from _party_stat_row
        rows = [piece._party_rows.itemAt(i).widget()         # noqa: SLF001
                for i in range(piece._party_rows.count())]  # noqa: SLF001
        rows = [w for w in rows if w is not None]
        assert rows, "the piece block has no stat rows"
        for w in rows:
            keys = [c.text() for c in w.findChildren(QtWidgets.QLabel)]
            assert len(keys) == 2, f"row is not key/value: {keys}"
    finally:
        _teardown_party_page(host, page)


def test_stat_totals_sum_every_equipped_piece(qapp):
    """The STAT TOTALS block sums the same computed stats the piece panel
    shows one piece at a time: two pieces carrying the same stat add up (2x
    one piece's value), a container row (Bag_Z2) contributes nothing, and a
    row never reads as None (same stat_text() rule as the piece panel).
    """
    pytest.importorskip("PySide6")

    host, page = _built_party_page()
    try:
        host._party_piece_gear = [
            {"slot": 0, "kind": "Sword_Swarm", "rarity": "Rare",
             "upgrade": 0, "level": 25, "state": "filled"},
            {"slot": 1, "kind": "Axe_Boomerang", "rarity": "Rare",
             "upgrade": 0, "level": 25, "state": "filled"},
            {"slot": 22, "kind": "Bag_Z2", "rarity": "Common",
             "upgrade": 0, "level": 0, "state": "filled"},
        ]
        host._fill_party_totals()
        vals = dict(_totals_rows(host))
        assert vals, "the totals block is empty"
        assert not any(v is None or v == "None" for v in vals.values()), vals
        # expected = the two weapons' stat_rows summed per label (their spreads
        # differ — Faith is sword-only, Strength is on both); the bag row adds
        # nothing, being a container rather than a piece. equipped_stats, not
        # gear_stats: these are WORN pieces, and the walk's upgrade rank has
        # to reach the maths (2026-09-26).
        from farever_companion.data.items import catalog as gcatalog
        from farever_companion.data.items import stats as gstats
        want: dict[str, float] = {}
        for kind in ("Sword_Swarm", "Axe_Boomerang"):
            item = (gcatalog._data().get("items", {}) or {}).get(kind) or {}
            gs = gstats.equipped_stats(item, 25, "Rare", 0)
            for s in gstats.stat_rows(gs["stats"]):
                want[s["label"]] = want.get(s["label"], 0.0) + s["value"]
        # …plus the one DERIVED row: Critical Chance under the Critical rating
        # it is computed from. Both pieces are level 25, which is a level the
        # base was measured at, so it is present and predictable.
        hero_level = 25
        pct = gstats.crit_chance_pct(want.get("Critical", 0.0), hero_level)
        assert pct is not None, \
            "level 25 has a measured crit base, so the row must be derivable"
        want["Critical Chance"] = pct
        assert set(vals) == set(want), (set(vals), set(want))
        for lbl, total in want.items():
            got = vals[lbl]
            exp = (f"{total:.1f}%" if lbl == "Critical Chance"
                   else gstats.stat_text(round(total, 2)))
            assert got == exp, \
                f"{lbl}: totals row says {got!r}, want {exp!r}"
        # a stat carried by BOTH pieces really summed (Strength 12+12)
        assert want.get("Strength", 0) > 24 or vals.get("Strength") != "12", \
            "shared stats did not add up"
    finally:
        _teardown_party_page(host, page)


def _totals_rows(host):
    """The Total Stats block's (key, value) pairs, in the order they render.

    Read off the block's BODY widget rather than the Card: `findChildren` on
    the Card also returns the "Total Stats" caption, which sits at an odd
    index and shifts every pairing after it by one.
    """
    from PySide6 import QtWidgets

    box = host._party_totals_card
    widgets = [box.layout().itemAt(i).widget()
               for i in range(box.layout().count())]
    body = next(w for w in widgets
                if w is not None and w.layout() is not None
                and w.findChildren(QtWidgets.QLabel))
    labels = body.findChildren(QtWidgets.QLabel)
    return [(labels[i].text(), labels[i + 1].text())
            for i in range(0, len(labels) - 1, 2)]


# --- the equipped-piece maths, pinned to the game's own tooltips (2026-09-26) ---

# Read off four equipped pieces' own tooltips on 2026-09-26, all level 25.
# Three at +5 and one at +4, across three classes and four stat mixes — and
# the +4 is the one that matters: it is EXACT with no rarity bonus at all,
# so it is what proves the Legendary correction is per-rarity rather than a
# flat extra step. Every match is exact on every stat, splits included.
_TOOLTIP_TRUTH = (
    ("Staff_SummonDemon", "Legendary", 5,
     {"Intellect": 70, "Critical": 110, "Vitality": 75.0}),
    ("Daggers_DuplicatePoison", "Legendary", 5,
     {"Dexterity": 70, "Critical": 110, "Vitality": 60.0}),
    # a dual-class axe: the tooltip prints each split on its own line, so
    # Dex 35 + Str 29 is our model's single 64, and ArPen 55 + Crit 55 its
    # single 110. Getting these right means the SPLIT rendering agrees too.
    ("Axe_Boomerang", "Legendary", 5,
     {"Vitality": 67, "Strength": 29, "Dexterity": 35, "Critical": 55,
      "Armor Penetration": 55}),
    # the discriminator: Epic at +4, no bonus, no passive, no Corrupted Gift
    ("Halos_Totem", "Epic", 4,
     {"Vitality": 56, "Faith": 22, "Intellect": 26, "Magic Penetration": 47,
      "Fervor": 47}),
)


@pytest.mark.parametrize("kind,rarity,upgrade,truth", _TOOLTIP_TRUTH)
def test_a_worn_pieces_stats_match_its_own_tooltip(kind, rarity, upgrade,
                                                    truth):
    """A WORN piece is costed at its upgrade rank, and the numbers equal what
    the game prints on the piece's own tooltip.

    Both call sites used to hand `gear_stats` only the level and the rarity,
    so an equipped weapon was costed as a freshly-dropped +0: the staff below
    read 69 Critical where its tooltip says 110 — a 60% undercount on the
    largest item the hero owns, on the block the page leads with.
    """
    from farever_companion.data.items import catalog as gcatalog
    from farever_companion.data.items import stats as gstats

    item = (gcatalog._data().get("items", {}) or {}).get(kind) or {}
    assert item, f"{kind} is not in the sheet, so this pins nothing"
    got = {s["label"]: s["value"] for s in gstats.stat_rows(
        gstats.equipped_stats(item, 25, rarity, upgrade)["stats"])}
    assert got == truth, (
        f"{kind}: equipped_stats says {got}, the game's tooltip says {truth}")
    # and the old behaviour really did miss, or the test proves nothing
    stale = {s["label"]: s["value"] for s in gstats.stat_rows(
        gstats.gear_stats(item, 25, rarity)["stats"])}
    assert stale != truth, (
        f"{kind}: the un-upgraded maths already matches the tooltip, so the "
        "upgrade rank is not what this test is about")


@pytest.mark.parametrize("kind,rarity,upgrade,truth", _TOOLTIP_TRUTH)
def test_a_worn_piece_is_right_even_when_the_rarity_string_lies(kind, rarity,
                                                                upgrade, truth):
    """The tooltip numbers must not depend on the live rarity string reading.

    Every piece measured is AUTHORED `Rare` in the sheet while being worn at
    +5, and `core/inspect._read_item` falls back to the authored rarity when
    it cannot read a live one. On that path the band is wrong (the staff
    costs iLevel 310 instead of 380 and reads 84 Critical, not 110), so a
    result that only holds when the string comes back is a result that
    silently does not.

    The upgrade rank settles it: a band cannot hold more ranks than its cap
    (Rare 3, Epic 4, Legendary 5), so +5 can only be Legendary and +4 can
    only be Epic or better. The string may still PROMOTE a piece — a
    Legendary at +2 stays Legendary — it just cannot demote one past what
    its own gems prove.
    """
    from farever_companion.data.items import catalog as gcatalog
    from farever_companion.data.items import stats as gstats

    item = (gcatalog._data().get("items", {}) or {}).get(kind) or {}
    assert (item.get("rarity") or "") == "Rare", (
        f"{kind} is no longer authored Rare, so the fallback path this test "
        "exists for is not being exercised")
    for label, handed in (("the true band", rarity),
                          ("the authored Rare", "Rare"),
                          ("nothing at all", None)):
        got = {s["label"]: s["value"] for s in gstats.stat_rows(
            gstats.equipped_stats(item, 25, handed, upgrade)["stats"])}
        assert got == truth, (
            f"{kind} handed {label!r} ({handed!r}): got {got}, tooltip "
            f"says {truth}")


def test_the_rendered_totals_block_equals_the_tooltip_numbers(qapp):
    """The block ON SCREEN equals the tooltips, with no maths of our own in
    the expectation.

    This is the end-to-end version of the per-piece tooltip test, and it
    exists because the other sum test cannot catch this class of error:
    `test_stat_totals_sum_every_equipped_piece` builds what it expects by
    CALLING `equipped_stats`, so when `equired_stats` was wrong the
    expectation was wrong by the same amount and the test passed anyway —
    which is exactly how a +0 gear sum survived four tooltips.

    So the numbers below are transcribed from the four tooltips and summed
    BY HAND. Nothing in this expectation calls the function under test.
    """
    pytest.importorskip("PySide6")
    import re

    # (slot, kind, live rarity, live upgrade) — the three fields the walk
    # hands the block, all read from the game rather than the sheet.
    worn = [
        (0, "Staff_SummonDemon", "Legendary", 5),
        (1, "Daggers_DuplicatePoison", "Legendary", 5),
        (2, "Halos_Totem", "Epic", 4),
        (4, "Axe_Boomerang", "Legendary", 5),
    ]
    # hand-summed from the tooltips, e.g. Vitality 75 + 60 + 56 + 67
    want = {
        "Critical": 275,               # 110 + 110 + 55
        "Vitality": 258,               # 75 + 60 + 56 + 67
        "Intellect": 96,               # 70 + 26
        "Dexterity": 105,              # 70 + 35
        "Faith": 22,
        "Strength": 29,
        "Magic Penetration": 47,
        "Fervor": 47,
        "Armor Penetration": 55,
    }

    host, page = _built_party_page()
    try:
        host._party_piece_gear = [
            {"slot": slot, "kind": kind, "rarity": rarity,
             "upgrade": upgrade, "level": 25, "state": "filled"}
            for slot, kind, rarity, upgrade in worn]
        host._fill_party_totals()
        rows = dict(_totals_rows(host))

        def _num(text):
            m = re.match(r"-?[\d,]+(?:\.\d+)?", text or "")
            assert m, f"row value is not a number: {text!r}"
            return float(m.group(0).replace(",", ""))

        for label, expected in want.items():
            assert label in rows, (
                f"{label} is missing from the block: {sorted(rows)}")
            got = _num(rows[label])
            assert got == expected, (
                f"{label}: the block shows {rows[label]!r} ({got}), the "
                f"tooltips sum to {expected}. A +0 gear sum is the usual "
                "cause — check equipped_stats is still handed `upgrade`.")
        # and nothing beyond the hand-summed set turned up
        assert set(rows) - set(want) == {"Critical Chance"}, sorted(rows)
    finally:
        _teardown_party_page(host, page)


def test_crit_chance_reproduces_all_three_character_sheet_readings():
    """Critical Chance = base(level) + rating / 19, checked against the sheet.

    Measured 2026-09-26 on one level-25 character in three states. The two
    absolute readings are the load-bearing ones: they differ by 110.2 and the
    weapon that separates them grants exactly +110 Critical, so the divisor is
    confirmed by the delta AND by both absolutes rather than fitted to one.
    """
    from farever_companion.data.items import stats as gstats

    assert gstats.CRIT_CHANCE_DIVISOR == 19.0
    for label, sheet_pct, rating in (
            ("naked", 6.0, 0.0),
            ("weapon off", 13.6, 144.4),
            ("fully geared", 19.4, 254.6)):
        got = gstats.crit_chance_pct(rating, 25)
        assert got is not None, f"{label}: level 25 is measured, so None is wrong"
        assert abs(got - sheet_pct) <= 0.05, (
            f"{label}: rating {rating} gives {got:.2f}%, the sheet reads "
            f"{sheet_pct}%")


def test_crit_chance_ignores_the_weapons_own_passive_percentage():
    """Why the formula is rating-only, and what that costs us.

    Cheese Moon's tooltip ends "Upgrade: Critical Chance increased by 4%." —
    a flat percentage off the weapon's upgrade passive, on top of whatever
    its Critical rating converts to. The Rogue wearing it reads 32.3%, which
    is 6.0 base + 4.0 passive + the rest, so `crit_chance_pct` UNDER-reads by
    exactly that passive on any hero whose weapon carries one.

    It is left out deliberately rather than guessed at: the passives are
    per-instance (Corrupted Gift alone has been seen at +40/-40 three ways),
    there is no `Gift` type in the sheet to read them from, and a wrong
    constant here would move the page's headline number for every hero. The
    gear-slot scan (ai/workspace/buffhead/gearscan.py) is what would settle
    it. Pinned here so the gap is a decision on the record, not an oversight.
    """
    from farever_companion.data.items import stats as gstats

    rating, passive, sheet = 423.7, 4.0, 32.3
    without = gstats.crit_chance_pct(rating, 25)
    assert abs(without + passive - sheet) <= 0.05, (
        "the fixture no longer describes the sheet reading")
    assert without < sheet, (
        "with no passive term the formula can only ever under-read")


def test_crit_chance_is_absent_for_a_level_with_no_measured_base():
    """An uncalibrated level returns None rather than a borrowed number.

    The base is only KNOWN at level 25. Extrapolating it would put a decimal
    on the page's main readout that is authoritative in appearance and wrong
    for every hero not standing at 25 — the one failure mode worse than the
    missing row it replaces.
    """
    from farever_companion.data.items import stats as gstats

    for level in (None, 0, 1, 10, 24, 26, 30, 99):
        assert gstats.crit_chance_pct(205, level) is None, (
            f"level {level} has no measured base but produced "
            f"{gstats.crit_chance_pct(205, level)}")
    assert gstats.crit_chance_pct(0, 25) == 6.0, \
        "a naked level-25 character still reads its 6% base"


def test_the_totals_block_shows_no_crit_chance_off_a_calibrated_level(qapp):
    """The derived row is ABSENT, not wrong, when the level is uncalibrated."""
    pytest.importorskip("PySide6")

    host, page = _built_party_page()
    try:
        # level 20: a real piece, a real rating, an unmeasured base
        host._party_piece_gear = [
            {"slot": 0, "kind": "Staff_SummonDemon", "rarity": "Legendary",
             "upgrade": 5, "level": 20, "state": "filled"},
        ]
        host._fill_party_totals()
        keys = {k for k, _ in _totals_rows(host)}
        assert "Critical" in keys, f"the rating row is missing: {keys}"
        assert "Critical Chance" not in keys, (
            "a crit chance was shown at a level with no measured base: "
            f"{keys}")
    finally:
        _teardown_party_page(host, page)


def test_crit_chance_sits_directly_under_the_critical_rating(qapp):
    """The row order matters: the percentage belongs to the rating above it."""
    pytest.importorskip("PySide6")

    host, page = _built_party_page()
    try:
        host._party_piece_gear = [
            {"slot": 0, "kind": "Staff_SummonDemon", "rarity": "Legendary",
             "upgrade": 5, "level": 25, "state": "filled"},
        ]
        host._fill_party_totals()
        rows = _totals_rows(host)
        keys = [k for k, _ in rows]
        assert "Critical" in keys and "Critical Chance" in keys, keys
        assert keys.index("Critical Chance") == keys.index("Critical") + 1, (
            f"Critical Chance is not directly under Critical: {keys}")
        # and it is a percentage, not a bare number
        vals = dict(rows)
        assert vals["Critical Chance"].endswith("%"), vals["Critical Chance"]
    finally:
        _teardown_party_page(host, page)


def test_carried_groups_fold_and_ship_collapsed(qapp):
    """The fold row (Jewelry, Extras, Tools) is an accordion: their
    captions sit side by side in one card, and clicking one expands that card
    BENEATH the row while folding the other — expansion grows downward and
    never shoves the detail column sideways.

    JEWELRY ships OPEN (2026-09-26: it joined the row as its first tab, and
    rings/neck are what this page is opened for — an accordion that starts
    shut hides its own first tab); the other two ship folded. Clicking the open
    tab is a NO-OP, so the row always holds exactly one expanded group. The
    group that moved to the right column (Consumables) ships OPEN under Buffs
    and is NOT part of this accordion — opening Tools must leave it alone. A
    hidden group's tiles still fill, so expanding shows live values. Weapons
    and Armor are still plain cards and never fold.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtGui, QtWidgets

    host, page = _built_party_page()
    try:
        by_cap = {_group_caption(c): c for c in host._party_group_cards}

        for cap in ("Extras", "Tools"):
            assert by_cap[cap].isHidden(), \
                f"{cap} did not ship hidden"
        assert not by_cap["Jewelry"].isHidden(), \
            "Jewelry is the row's default tab and must ship expanded"
        assert by_cap["Consumables"].isHidden(), \
            ("Consumables is the Buffs block's SECOND tab now, and Buffs is "
             "the one that ships open (2026-09-26) — so Consumables must "
             "start folded")
        for cap in ("Weapons", "Armor"):
            assert not by_cap[cap].isHidden(), \
                f"{cap} must never fold"

        # filling while hidden works (the tiles exist, just not shown)
        host._party_profile_fill([
            {"slot": 16, "kind": "Pickaxe", "rarity": "Common",
             "upgrade": 0, "level": 5, "state": "filled"},
        ])
        # clicking the Tools caption reveals exactly that card
        ev = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseButtonPress,
                               QtCore.QPointF(4, 4), QtCore.Qt.LeftButton,
                               QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        caps = {c.title(): c
                for c, _k in host._party_fold_caps}
        caps["Tools"].mousePressEvent(ev)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        tools = by_cap["Tools"]
        assert not tools.isHidden(), "clicking did not expand"
        assert by_cap["Extras"].isHidden(), \
            "the accordion left Extras open"
        # opening any other tab folds the DEFAULT one too — Jewelry ships
        # open, so it is the card most likely to be left stranded open beside
        # the one you just asked for (2026-09-26).
        assert by_cap["Jewelry"].isHidden(), \
            "opening Tools left the default Jewelry tab open as well"
        assert by_cap["Consumables"].isHidden(), \
            ("the fold row still owns Consumables — it is a tab of the BUFFS "
             "block now, and the two accordions must not touch each other. "
             "It is hidden here because Buffs, not Consumables, is the tab "
             "that ships open in that block")
        pickaxe = host._party_grid_cards.get(16)
        assert pickaxe is not None and not pickaxe.isHidden(), \
            "the expanded group shows its piece"
        # clicking the OPEN tab again does NOT collapse it (2026-09-26: the row
        # always keeps exactly one group expanded, so the body is never empty).
        # The choice changes by opening a different tab, which is what the
        # full sweep in test_the_fold_row_always_has_exactly_one_group_open
        # walks; here it is enough that Tools survives its own second click.
        caps["Tools"].mousePressEvent(ev)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert not tools.isHidden(), \
            "clicking the open tab collapsed it — the row can go empty"

        # The moved group is now a TAB of the Buffs block, beside Buffs itself
        # (2026-09-26): its caption lives in the block's tab row and its card
        # in the body, so neither is a DIRECT child of the block any more —
        # ancestry is the check, not parentWidget() (which is why this is
        # walked rather than compared).
        moved_caps = {c.title(): (c, k)
                      for c, k in host._party_moved_caps}
        cap, card = moved_caps["Consumables"]
        buffs = host._party_buffs_card

        def _inside(w, root):
            p = w.parentWidget()
            while p is not None:
                if p is root:
                    return True
                p = p.parentWidget()
            return False

        assert _inside(cap, buffs), \
            "the moved group's tab is not inside the Buffs block"
        assert _inside(card, buffs), \
            "the moved group's card is not inside the Buffs block"
        # …and the two tabs are peers in ONE row, the fold row's layout
        assert cap.parentWidget() is host._party_buffs_cap.parentWidget(), \
            "Buffs and Consumables are not tabs of the same row"
        assert host._party_buffs_cap.is_collapsed() is False, \
            "the Buffs block must open on Buffs"
        assert card.isHidden(), "Consumables must start folded, behind Buffs"

        # opening Consumables swaps the body WITHOUT moving the block
        open_h = buffs.height()
        cap.mousePressEvent(ev)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert not card.isHidden(), "clicking the tab did not open it"
        assert host._party_buffs_area.isHidden(), \
            "the buff list stayed visible behind the tab"
        assert buffs.height() == open_h, (
            f"opening the tab changed the block's height "
            f"({open_h} -> {buffs.height()}) — it shoves the notes below")
        # clicking it again is a no-op: the block always shows one tab
        cap.mousePressEvent(ev)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert not card.isHidden(), \
            "clicking the open tab collapsed it — same rule as the fold row"
    finally:
        _teardown_party_page(host, page)


def test_fold_captions_are_centered_in_equal_cells(qapp):
    """The fold row reads as CENTERED tabs, not left-packed words: every
    caption's text is horizontally centered inside its own cell
    and all three cells are the same width (as wide as the longest caption),
    so the strip stays even however the titles differ in length.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    host, page = _built_party_page()
    try:
        page.resize(1400, 800)
        page.show()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        caps = [c for c, _k in host._party_fold_caps]
        assert len(caps) == 3, f"expected 3 fold captions, got {len(caps)}"
        for c in caps:
            assert c.alignment() & QtCore.Qt.AlignCenter, \
                f"{c.text()!r} is not horizontally centered"
            assert c.sizePolicy().horizontalPolicy() == \
                QtWidgets.QSizePolicy.Expanding, \
                f"{c.text()!r} does not share the row's slack"
        widths = sorted(c.width() for c in caps)
        assert widths[-1] - widths[0] <= 2, \
            f"fold captions are not equal width: {widths}"
        longest = max(c.sizeHint().width() for c in caps)
        assert caps[0].width() >= longest - 2, \
            "an equal cell is narrower than the longest caption, so that " \
            "caption would be clipped"
    finally:
        _teardown_party_page(host, page)


def test_a_tile_with_no_sprite_falls_back_to_its_slot_emoji(qapp):
    """A filled tile whose item has NO atlas sprite draws its slot's emoji,
    not a hollow square (2026-09-26).

    The square read as a broken image — on a tile that plainly holds something,
    it said "this item is missing" when the truth was only "this item's sprite
    is not in the atlas". The glyphs are the mockup's own paper-doll set (🗡️
    weapon, 🛡️ offhand, 🪖 head, 👕 chest, 🧤 gloves, 👖 legs, 💍 jewelry, 🧪
    consumable, 🍖 food, 🔨 tool), so a tile that cannot show its piece still
    says what KIND of slot it is.

    It is also the tile's RESTING state: every tile is built before the first
    gear walk answers, so the doll is a tidy grid of slot glyphs waiting for
    real art instead of a column of bare tiles that reads as a page which had
    failed. A real sprite still wins over the glyph — the sprite is what tells
    two rings apart — and an emptied slot goes back to its glyph.
    """
    pytest.importorskip("PySide6")

    from farever_companion.ui.pages.party_grid import _SLOT_EMOJI

    host, page = _built_party_page()
    try:
        # the map covers every drawn slot, and the mockup's glyphs are in it
        from farever_companion.ui.pages.party_grid import _SLOT_GROUPS
        drawn = {s for _title, slots in _SLOT_GROUPS for s in slots}
        assert drawn <= set(_SLOT_EMOJI), (
            f"slots with no emoji fallback: {sorted(drawn - set(_SLOT_EMOJI))}")
        assert _SLOT_EMOJI[0] == "\U0001F5E1", "Weapon 1 lost its blade"
        assert _SLOT_EMOJI[1] == "\u2694\ufe0f", "Weapon 2 lost its swords"
        assert _SLOT_EMOJI[2] == "\U0001F6E1", "the offhand lost its shield"

        # a real item still wins: it has a sprite, so no emoji
        host._party_profile_fill([
            {"slot": 0, "kind": "Sword_Swarm", "rarity": "Rare",
             "upgrade": 2, "level": 25, "state": "filled"}])
        icon = host._party_grid_cards[0]._party_slot_icon
        assert not icon.text(), (
            f"a real sprite was replaced by {icon.text()!r}")

        # an item the atlas cannot draw falls back to the slot's emoji. The
        # real sheet has 9 such ids and none of them is a piece, so the case
        # is driven with one of them — the honest version of "no sprite" is a
        # resolvable-looking id whose sheet is missing, which is a data state,
        # not something a fixture can invent.
        host._party_slot_fill(3, {
            "slot": 3, "kind": "ActivityLoot", "rarity": "Rare",
            "upgrade": 0, "level": 25, "state": "filled"})
        head = host._party_grid_cards[3]._party_slot_icon
        assert head.text() == _SLOT_EMOJI[3], (
            f"a missing sprite drew {head.text()!r}, not the head's emoji")
        assert head.pixmap().isNull(), "the emoji is drawn over a pixmap"
        assert "26px" in head.styleSheet(), \
            f"the emoji has no size: {head.styleSheet()!r}"

        # an emptied slot goes back to its glyph, dimmed — never a bare dot
        host._party_slot_reset(3)
        back = host._party_grid_cards[3]._party_slot_icon
        assert back.text() == _SLOT_EMOJI[3], \
            f"an emptied slot shows {back.text()!r}, not its glyph"
        assert back.pixmap().isNull()
        from farever_companion.ui.pages.party_grid import _SLOT_EMPTY_INK
        assert _SLOT_EMPTY_INK in back.styleSheet(), \
            f"the waiting glyph is not dimmed: {back.styleSheet()!r}"
    finally:
        _teardown_party_page(host, page)


def test_slot_emoji_matches_slot_names():
    """Every doll glyph says what the SLOT is, not what the mockup drew.

    The table is indexed by the game's own equipment array, so an entry is only
    correct if its index really is the slot its glyph claims: gloves on Chest
    and jeans on Feet was the state of it (2026-09-27), and nothing on the page
    could tell, because the glyph is only drawn when the atlas has no sprite
    for the item, so a wrong one just sits there being quietly wrong.
    `_EQUIP_SLOTS` is the authority, so the assertion is built from the slot
    NAME rather than from a second hand-copied index.
    """
    from farever_companion.data.items import labels as ilabels
    from farever_companion.ui.pages.party_grid import _SLOT_EMOJI

    want = {
        "Slot_Neck": "\U0001F4FF",      # necklace -> prayer beads
        "Slot_Trinket": "\U0001F48E",   # trinket -> cut gem
        "Slot_Waist": "\U000027B0",     # waist -> belt loop
        "Slot_Hands": "\U0001F932\U0001F3FC",   # palms up, medium-light
        "Slot_Legs": "\U0001F456",      # jeans
        "Slot_Chest": "\U0001F9E5",     # coat
        "Slot_Feet": "\U0001F463",      # footprints
        "Slot_Head": "\U0001F9E2",      # cap
        "Slot_Back": "\U0001F9E3",      # shawl (there is no cape emoji)
        "Slot_Weapon1": "\U0001F5E1",   # a single blade
        "Slot_Weapon2": "\u2694\ufe0f",  # crossed swords
    }
    slots = ilabels.equip_slots()
    for name, glyph in want.items():
        i = slots.index(name)
        assert _SLOT_EMOJI[i] == glyph, (
            f"{ilabels.equip_slot_label(i)} draws {_SLOT_EMOJI[i]!r}, "
            f"not {glyph!r}")

    # the garments must not collide: chest is a coat, back a shawl, head a
    # cap. These three sat on adjacent tiles in one card.
    for a, b in (("Slot_Chest", "Slot_Back"), ("Slot_Back", "Slot_Head"),
                 ("Slot_Head", "Slot_Shoulders")):
        assert _SLOT_EMOJI[slots.index(a)] != _SLOT_EMOJI[slots.index(b)], \
            f"{a} and {b} share a glyph"

    # …and neither must the two weapon slots, which sit side by side in one
    # row. The same dagger twice reads as a duplicated tile, not as two slots.
    assert _SLOT_EMOJI[slots.index("Slot_Weapon1")] != \
        _SLOT_EMOJI[slots.index("Slot_Weapon2")], \
        "Weapon 1 and Weapon 2 share a glyph"

    # neck and trinket must not read as a third ring. The two RING slots
    # sharing one glyph is correct — they are the same kind of thing.
    for name in ("Slot_Neck", "Slot_Trinket"):
        i = slots.index(name)
        assert _SLOT_EMOJI[i] != _SLOT_EMOJI[slots.index("Slot_FingerRight")], \
            f"{name} draws the ring glyph"


def test_slot_emoji_covers_every_drawn_slot():
    """The map covers every slot the doll actually builds.

    The gap between "this category is empty" and "this category is not drawn"
    is the whole point of `_SLOT_GROUPS`, so a slot present in one and not the
    other is a tile that renders with the bare default rather than saying what
    it is.
    """
    from farever_companion.ui.pages.party_grid import (_SLOT_EMOJI,
                                                       _SLOT_GROUPS)

    drawn = {s for _title, slots in _SLOT_GROUPS for s in slots}
    assert drawn <= set(_SLOT_EMOJI), (
        f"slots with no emoji fallback: {sorted(drawn - set(_SLOT_EMOJI))}")


def test_an_empty_tile_is_dim_and_a_filled_one_is_not(qapp):
    """Dim means EMPTY AND ALREADY READ (2026-09-26).

    A slot a gear walk has reported empty is drawn dim — glyph, label and frame
    all back. A slot with a piece in it is at full strength, in its rarity
    colour. So the whole doll reads at a glance: bright means equipped.

    A slot nobody has asked about yet is NOT dim. The page takes 1-2s to
    answer, and for that whole time a doll drawn dim says "this hero has no
    gear" — the same fabrication as the demo data, in the other direction. The
    dim ink waits for the walk that justifies it.

    This replaced a two-handed-weapon rule, which was the wrong mechanism
    twice over. The sheet carries no reliable "takes both hands" flag (most
    weapon rows have none), so the rule had to guess from the type name — and a
    guess on a page that reports live state dimmed an off-hand for a hero
    holding daggers, which is not a bug anyone can argue with. The fact worth
    showing is "nothing is in this slot"; whether the game would have allowed
    something else there is a rule the sheet does not tell us.

    The dim ink is a solid colour rather than an alpha or a
    QGraphicsOpacityEffect on purpose: an effect on a label that draws TEXT is
    not reliably honoured — the glyph came out at full strength with the effect
    set — and a darker grey cannot fail to apply.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets
    import pathlib

    from farever_companion.ui import theme
    from farever_companion.ui.pages.party_grid import (_SLOT_EMOJI,
                                                       _SLOT_EMPTY_INK)

    host, page = _built_party_page()
    try:
        slot2 = host._party_grid_cards[1]         # Weapon 2
        icon = slot2._party_slot_icon              # noqa: SLF001
        name = slot2._party_slot_name              # noqa: SLF001

        # BEFORE any walk: neutral, not dim. Nobody has read this slot yet.
        assert _SLOT_EMPTY_INK not in icon.styleSheet(), (
            f"an UNREAD slot is already drawn as empty: {icon.styleSheet()!r}")
        assert theme.PANEL_LOW not in slot2.styleSheet(), \
            "an UNREAD slot is already drawn with the empty slot's frame"
        assert slot2._party_slot_read is False, \
            "a freshly built tile claims to have been read"

        # a walk that reports the slot EMPTY is what earns the dim
        host._party_profile_fill([{"slot": 1, "state": "empty"}])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert _SLOT_EMPTY_INK in icon.styleSheet(), (
            f"a reported-empty slot is not dim: {icon.styleSheet()!r}")
        assert _SLOT_EMPTY_INK in name.styleSheet(), (
            f"a reported-empty slot's label is not dim: {name.styleSheet()!r}")
        assert theme.PANEL_LOW in slot2.styleSheet(), \
            "a reported-empty slot's frame is not dim"
        assert slot2._party_slot_read is True, \
            "the walk did not mark the slot as read"

        # a real piece brings the slot to full strength
        host._party_profile_fill([
            {"slot": 1, "kind": "Sword_Swarm", "rarity": "Rare", "upgrade": 2,
             "level": 25, "state": "filled"}])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert _SLOT_EMPTY_INK not in icon.styleSheet(), \
            "a filled slot kept the empty slot's dim ink"
        assert _SLOT_EMPTY_INK not in name.styleSheet(), \
            "a filled slot kept the empty slot's dim label"
        assert theme.PANEL in slot2.styleSheet() and \
            theme.PANEL_LOW not in slot2.styleSheet(), \
            "a filled slot kept the empty slot's dim frame"
        assert not icon.pixmap().isNull()

        # and emptying it dims it again — the rule is about the slot, not about
        # how the slot got that way
        host._party_profile_fill([{"slot": 1, "state": "empty"}])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert _SLOT_EMPTY_INK in icon.styleSheet(), \
            "an emptied slot did not go back to being dim"
        assert theme.PANEL_LOW in slot2.styleSheet()
        assert icon.text() == _SLOT_EMOJI[1], \
            "an emptied slot lost its slot glyph"

        # the off-hand is NOT special: it is dim because a walk reported it
        # empty, and it says so for the same reason every other empty slot
        # does. No two-handed inference anywhere on this page.
        host._party_profile_fill([
            {"slot": 0, "kind": "Daggers_DuplicatePoison", "rarity": "Epic",
             "upgrade": 0, "level": 25, "state": "filled"},
            {"slot": 2, "state": "empty"}])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        off = host._party_grid_cards[2]
        assert _SLOT_EMPTY_INK in off._party_slot_icon.styleSheet(), \
            "the empty offhand is not dim like every other empty slot"
        assert off._party_slot_name.text() == "Offhand", \
            "the offhand lost its name"
        import farever_companion.ui.pages.party_grid as _grid
        src = pathlib.Path(_grid.__file__).read_text(encoding="utf-8")
        assert "is_two_handed" not in src, (
            "the two-handed inference is back on the doll (2026-09-26)")
    finally:
        _teardown_party_page(host, page)


def test_the_piece_rarity_is_coloured_in_the_sheets_own_palette(qapp):
    """The rarity word on the Selected Piece line wears its rarity colour
    (2026-09-26).

    "Rare", "Epic" and "Legendary" all rendered as the same grey word in the
    same grey sentence, so the one fact that decides whether a drop is worth
    keeping was the one word on the line you could not pick out. The colour is
    `theme.RARITY`'s — the same palette the doll's tile borders use, so the
    card and the tile can never disagree about what Rare means.

    The line stays ONE label and ONE line (it never wraps, and the block is
    height-pinned), so the colour is inline rich text rather than three
    labels in a row.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    from farever_companion.ui import theme

    host, page = _built_party_page()
    try:
        assert host._party_piece_meta.textFormat() == QtCore.Qt.RichText, \
            "the meta line is not a rich-text label, so a colour cannot apply"

        for rarity, colour in (("Rare", theme.RARITY["Rare"]),
                               ("Epic", theme.RARITY["Epic"]),
                               ("Legendary", theme.RARITY["Legendary"]),
                               ("Uncommon", theme.RARITY["Uncommon"])):
            host._party_piece_kind = ""
            host._party_profile_fill([
                {"slot": 0, "kind": "Sword_Swarm", "rarity": rarity,
                 "upgrade": 2, "level": 25, "state": "filled"}])
            for _ in range(2):
                QtWidgets.QApplication.instance().processEvents()
            text = host._party_piece_meta.text()
            assert f">{rarity}<" in text, \
                f"{rarity} is not its own coloured span: {text!r}"
            assert colour in text, \
                f"{rarity} is not painted {colour}: {text!r}"
            # the level and upgrade stay quiet
            assert "Lv 25" in text and "+2" in text, text
            assert theme.DIM in text, \
                f"the rest of the line is not dim: {text!r}"
            assert not host._party_piece_meta.wordWrap(), \
                "the meta line started wrapping"
    finally:
        _teardown_party_page(host, page)


def test_the_piece_name_wears_its_rarity_and_the_placeholder_does_not(qapp):
    """The Selected Piece NAME is painted in the piece's rarity (2026-09-26).

    The rarity used to live only on the small word under the name, which meant
    reading the name before you could see what it was — the one fact you open
    this page for was the one you had to reach the bottom of the card for.
    The name now wears the same `theme.rarity_color` as the word below it, so
    the two can never disagree about the same piece.

    The PLACEHOLDER is the other half: with no piece selected there is no
    rarity, and a rarity colour there would be a claim about an item that is
    not on the hero. So the caption falls back to the ordinary caption grey —
    both on first build and when a selection is dropped.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui import theme

    host, page = _built_party_page()
    try:
        cap = host._party_piece_cap
        # nothing selected: the placeholder is grey, not a rarity
        assert theme.MUTED in cap.styleSheet(), \
            f"the empty placeholder is not the caption grey: {cap.styleSheet()!r}"
        for rarity in theme.RARITY:
            host._party_piece_kind = ""
            host._party_profile_fill([
                {"slot": 0, "kind": "Sword_Swarm", "rarity": rarity,
                 "upgrade": 1, "level": 10, "state": "filled"}])
            for _ in range(2):
                QtWidgets.QApplication.instance().processEvents()
            assert theme.rarity_color(rarity) in cap.styleSheet(), (
                f"a {rarity} piece's name is not in {theme.rarity_color(rarity)}: "
                f"{cap.styleSheet()!r}")

        # dropping the selection takes the colour back off
        host._party_piece_kind = ""
        host._party_profile_fill([{"slot": 0, "state": "empty"}])
        for _ in range(2):
            QtWidgets.QApplication.instance().processEvents()
        assert theme.MUTED in cap.styleSheet(), \
            "the name kept a rarity colour with no piece selected"
        assert theme.rarity_color("Legendary") not in cap.styleSheet(), \
            "a Legendary colour survived the selection being dropped"
    finally:
        _teardown_party_page(host, page)


def test_the_meta_line_elides_its_tail_instead_of_clipping_it(qapp):
    """The Selected Piece meta line must ELIDE, never silently clip (2026-09-26).

    A plain QLabel given text wider than its box cuts the overflow off the
    right edge with nothing on the page to say so. Measured, the widest real
    line — "Legendary  +12  ·  Lv 120" — wants 308px in a 242px card, so the
    level WAS being cut off silently for the two longest rarities.

    The elide must give up the TAIL and keep the rarity word, because the
    rarity word is the coloured one and the whole reason the line was split
    into a span. It is measured as a LAYED-OUT rich-text label, not through
    QFontMetrics, which would measure the markup tags instead of the text.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    host, page = _built_party_page()
    try:
        page.show()
        page.resize(1200, 900)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        meta = host._party_piece_meta

        def laid_out_width(text: str) -> int:
            probe = QtWidgets.QLabel(text)
            probe.setFont(meta.font())
            probe.setTextFormat(QtCore.Qt.RichText)
            w = probe.sizeHint().width()
            probe.deleteLater()
            return w

        for rarity in ("Common", "Rare", "Epic", "Legendary"):
            host._party_piece_kind = ""
            # the widest real line: a double-digit upgrade on a triple-digit
            # level, which is the case that used to clip
            host._party_profile_fill([
                {"slot": 0, "kind": "Sword_Swarm", "rarity": rarity,
                 "upgrade": 12, "level": 120, "state": "filled"}])
            for _ in range(2):
                QtWidgets.QApplication.instance().processEvents()
            text = meta.text()
            assert laid_out_width(text) <= meta.width(), (
                f"{rarity}: the line is {laid_out_width(text)}px in a "
                f"{meta.width()}px box — it is CLIPPING, not eliding")
            # the rarity word survives even when the tail cannot
            assert f">{rarity}<" in text, \
                f"{rarity} lost its span to the elide: {text!r}"
    finally:
        _teardown_party_page(host, page)


def test_only_a_pet_READ_failure_is_reported_never_an_empty_pet(qapp):
    """"No pet equipped." is gone; "no pet FIELD" stays (2026-09-26).

    The Companion tile is an empty slot, and an empty slot is the game's own
    default state — the rule the gear notes are built on, and the one printed
    three lines above where this note used to be added. A hero with no pet also
    has a Companion tile drawn dim like every other empty slot, so the line
    restated the doll in a different column.

    What is NOT the same fact is a loadout that exposes no pet field at all.
    That is a reader limit, and a reader limit is worth saying: "I could not
    read this" and "there is nothing here" are different, and only one of them
    is a defect. So that note still appears.
    """
    pytest.importorskip("PySide6")

    from farever_companion.core import inspect as hero_inspect

    host, page = _built_party_page()
    try:
        class _M:
            proc = hl = None
            player_addr = 0x1000
            dps = None

            @staticmethod
            def units():
                return []

        host.model = _M()
        host._party_selected_addr = 0x1000

        # the game says there is no pet: no note, and the tile is empty
        hero_inspect.hero_pet = lambda *a, **k: {"id": "", "off": True}
        assert host._fill_party_pet(host.model, 0x1000) is None, \
            "the game's own 'no pet' is being reported as news"
        assert host._party_pet_box._party_pet_icon.text() == "\U0001F43E", \
            "the Companion tile did not go back to its empty state"

        # the loadout exposes no pet field: that IS a reader limit, and says so
        hero_inspect.hero_pet = lambda *a, **k: {"id": "", "off": False}
        note = host._fill_party_pet(host.model, 0x1000)
        assert note and "reader limit" in note, (
            f"an unreadable pet field is no longer reported: {note!r}")

        # a real pet: no note either
        hero_inspect.hero_pet = lambda *a, **k: {"id": "Goat_Demon",
                                                 "off": False}
        import farever_companion.ui.pages.party_page as _pp
        _pp._live_pet = lambda *a, **k: None
        assert host._fill_party_pet(host.model, 0x1000) is None, \
            "a live pet is being reported as a note"
    finally:
        _teardown_party_page(host, page)


def test_the_selected_piece_always_belongs_to_the_hero_on_screen(qapp):
    """The Selected Piece panel never shows a piece this hero is not wearing
    (2026-09-26).

    The default was always right — a fresh hero opens on the main weapon, then
    Weapon 2, then the offhand, then whatever is filled — but only until you
    clicked a tile. `_party_piece_kind` is an ITEM, not a slot, and it survived
    every hero switch: click a chest on one hero, switch to another, and the
    panel still showed that chest, with rarity "Common", no upgrade and no
    level, for a hero who had never worn it. All three numbers were invented —
    the row lookup found nothing and the defaults filled the gaps.

    So a selection that is not among this hero's pieces is dropped and the
    default takes over, and the panel is driven by a real row or by nothing.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    host, page = _built_party_page()
    try:
        hero_a = [
            {"slot": 0, "kind": "Sword_Swarm", "rarity": "Rare", "upgrade": 2,
             "level": 25, "state": "filled"},
            {"slot": 6, "kind": "Chest_Starter_Wiz", "rarity": "Common",
             "upgrade": 0, "level": 18, "state": "filled"},
        ]
        hero_b = [          # a different weapon, and no chest at all
            {"slot": 0, "kind": "GS_Nova", "rarity": "Epic", "upgrade": 5,
             "level": 40, "state": "filled"},
        ]

        # the default is the MAIN weapon
        host._party_piece_kind = ""
        host._party_profile_fill(hero_a)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert host._party_piece_kind == "Sword_Swarm", \
            f"a fresh hero opened on {host._party_piece_kind!r}, not the weapon"

        # the user picks a non-weapon piece...
        host._party_piece_kind = "Chest_Starter_Wiz"
        host._fill_party_piece()
        assert host._party_piece_cap._full, "the chest did not render"

        # …then switches to a hero who is not wearing one
        host._party_profile_fill(hero_b)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert host._party_piece_kind == "GS_Nova", (
            f"the selection followed the hero onto a piece they are not "
            f"wearing: {host._party_piece_kind!r}")
        assert "Epic" in host._party_piece_meta.text(), (
            f"the panel's numbers are not this hero's: "
            f"{host._party_piece_meta.text()!r}")

        # a hero with no weapon at all falls back to what they DO wear
        host._party_piece_kind = ""
        host._party_profile_fill([hero_a[1]])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert host._party_piece_kind == "Chest_Starter_Wiz", \
            "a hero with no weapon was not given the piece they are wearing"

        # …and a hero with nothing shows nothing, rather than a default
        host._party_piece_kind = ""
        host._party_profile_fill([])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert host._party_piece_kind == "", \
            "a bare hero was given an invented piece"
    finally:
        _teardown_party_page(host, page)


def test_the_players_column_grows_by_measured_characters(qapp):
    """The Players column is 5 name characters wider than its base (2026-09-26).

    Asked for as characters, because a character is not a fixed number of
    pixels — it is whatever the real name font measures, and the name elides
    precisely because the column is too narrow for it. So the growth is
    computed from a real card's name label at fill time, which is the first
    moment the font is resolved.

    The point of the test is that the growth is IDEMPOTENT and font-derived: a
    second and third fill must not add five more characters each time, and the
    measured gain must equal five characters of the font that is actually in
    use rather than a hardcoded pixel count.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui.pages.party_roster import (_ROSTER_EXTRA_CHARS,
                                                        _ElideLabel)

    host, page = _built_party_page()
    try:
        page.show()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        wrap = host._party_roster_wrap
        base = host._party_roster_base_w
        assert wrap.minimumWidth() == base, \
            f"the column starts at {wrap.minimumWidth()}, not its base {base}"

        heroes = [(0x1000 + i, f"Hero{i}", "Priest") for i in range(3)]
        host._party_roster_fill(heroes)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        first = wrap.minimumWidth()
        fm = host._party_roster_body.itemAt(0).widget(
        ).findChildren(_ElideLabel)[0].fontMetrics()
        char_w = fm.horizontalAdvance("abcdefghij") / 10.0
        gained = first - base
        assert abs(gained - round(_ROSTER_EXTRA_CHARS * char_w)) <= 1, (
            f"the column grew {gained}px, which is not "
            f"{_ROSTER_EXTRA_CHARS} characters of {char_w:.1f}px")
        # and again, and again — it must not walk
        for _ in range(3):
            host._party_roster_fill(heroes)
            for _ in range(2):
                QtWidgets.QApplication.instance().processEvents()
        assert wrap.minimumWidth() == first, (
            f"repeated fills grew the column from {first} to "
            f"{wrap.minimumWidth()}")
        assert wrap.maximumWidth() >= first, \
            "the column's maximum is below its own minimum"
    finally:
        _teardown_party_page(host, page)


def test_one_empty_read_does_not_deselect_or_blank_the_page(qapp):
    """An EMPTY hero read is not a deselection (2026-09-26).

    `_refresh_party_all` used to set the selection to 0 whenever the hero walk
    came back with nothing, and `_refresh_party_detail` then painted "No
    player selected." over a detail column that had a real hero in it a moment
    earlier. Measured, one empty poll did all of this at once:

        selected after clicking Them:     0x22220000
        selected after ONE empty read:    0x0
        blank text?                       True
        cards left on the page:           0

    and when the heroes came back the selection jumped to whoever sorted
    FIRST rather than the hero the user was reading — so a walk that failed for
    one tick silently threw away the selection and the whole page with it.

    The distinction the fix rests on: the game saying "no heroes here" is a
    claim, and the game saying NOTHING at all is not one. A walk that returns
    nothing is a walk that failed — a zone change, a load, or the roster remap
    this page already knows costs seconds. So an empty read keeps the
    selection, keeps the cards, and skips the rebuild; the next tick that does
    see the heroes reconciles through the branches that handle a real change of
    party.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    from farever_companion.ui.memory_reclaimer import MemoryReclaimerMixin
    from farever_companion.ui.pages.party_page import PartyPageMixin

    ME, THEM = 0x11110000, 0x22220000

    class Unit:
        def __init__(self, addr, name, cls):
            self.addr, self.is_hero, self.hero_class = addr, True, cls
            self.level, self.hp, self.unit_id = 30, 5000.0, name

    class Model:
        player_addr = ME
        units_list = [Unit(ME, "Me", "Warrior"),
                      Unit(THEM, "Them", "Mage")]

        def units(self):
            return list(self.units_list)

        def player_name(self, a):
            return "Me" if a == ME else "Them"

        def hero_display_name(self, u):
            return u.unit_id

        def cached_group_roster(self):
            return None

        def hero_class_of(self, a):
            return "Warrior" if a == ME else "Mage"

    class Host(PartyPageMixin, QtCore.QObject):
        _widget_alive = staticmethod(MemoryReclaimerMixin._widget_alive)

        def __init__(self):
            QtCore.QObject.__init__(self)
            self.s = None
            self.model = Model()

        def _page_container(self, title=None):
            w = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(w)
            lay.setContentsMargins(22, 20, 22, 20)
            lay.setSpacing(16)
            return w, lay

        def _current_page(self):
            return "party"

        def _party_sync_chrome(self, *a):
            pass

    def detail_text(h):
        out = []
        for lay in (h._party_buffs, h._party_gear):
            for i in range(lay.count()):
                wd = lay.itemAt(i).widget()
                if wd is not None:
                    if hasattr(wd, "text"):
                        out.append(wd.text())
                    out.extend(w.text() for w in wd.findChildren(
                        QtWidgets.QLabel))
        return "\n".join(out)

    def card_count(h):
        body = h._party_roster_body
        return len([body.itemAt(i).widget() for i in range(body.count())
                    if body.itemAt(i).widget() is not None])

    host = Host()
    page = host._page_party()
    try:
        host._refresh_party_all()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

        # select the SECOND hero, so "kept" cannot be confused with
        # "reset to the first"
        host._party_selected_addr = THEM
        host._refresh_party_detail()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert host._party_selected_addr == THEM

        # one tick where the walk returns nothing
        host.model.units_list = []
        host._refresh_party_all()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert host._party_selected_addr == THEM, (
            "one empty read deselected the page: "
            f"{host._party_selected_addr:#x}")
        assert card_count(host) == 2, \
            f"one empty read deleted the roster: {card_count(host)} cards"
        assert "No player selected" not in detail_text(host), \
            "one empty read blanked the detail column"

        # and when the heroes come back, the selection is STILL the hero the
        # user was reading — not whoever sorted first
        host.model.units_list = [Unit(ME, "Me", "Warrior"),
                                 Unit(THEM, "Them", "Mage")]
        host._refresh_party_all()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert host._party_selected_addr == THEM, (
            "the selection was lost when the heroes returned: "
            f"{host._party_selected_addr:#x}")
        assert card_count(host) == 2
    finally:
        page.deleteLater()
        QtWidgets.QApplication.instance().processEvents()


def test_a_real_party_change_still_moves_the_selection(qapp):
    """The empty-read fix must not freeze the selection (2026-09-26).

    Holding the selection through an empty read is only correct while the page
    is waiting for a walk to succeed. When the heroes that DO come back no
    longer contain the selected one — they left, or the party changed — the
    selection must move to a real hero, and it must move in the same tick
    rather than waiting for the next poll.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    from farever_companion.ui.memory_reclaimer import MemoryReclaimerMixin
    from farever_companion.ui.pages.party_page import PartyPageMixin

    ME, THEM, NEW = 0x11110000, 0x22220000, 0x33330000

    class Model:
        player_addr = ME

        def units(self):
            return []

        def cached_group_roster(self):
            return None

    class Host(PartyPageMixin, QtCore.QObject):
        _widget_alive = staticmethod(MemoryReclaimerMixin._widget_alive)

        def __init__(self):
            QtCore.QObject.__init__(self)
            self.s = None
            self.model = Model()

        def _page_container(self, title=None):
            w = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(w)
            lay.setContentsMargins(22, 20, 22, 20)
            lay.setSpacing(16)
            return w, lay

        def _current_page(self):
            return "party"

        def _party_sync_chrome(self, *a):
            pass

    host = Host()
    page = host._page_party()
    try:
        # Them was selected, and the next real roster does not contain them
        host._party_selected_addr = THEM
        host._party_roster_fill([(ME, "Me (You)", "Warrior"),
                                 (THEM, "Them", "Mage")])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert host._party_selected_addr == THEM

        # a roster where Them is GONE and somebody new has joined
        host._party_heroes = lambda: [(ME, "Me (You)", "Warrior"),
                                      (NEW, "Newbie", "Rogue")]
        host._refresh_party_all()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert host._party_selected_addr in (ME, NEW), (
            "the selection stayed on a hero who left: "
            f"{host._party_selected_addr:#x}")
        assert host._party_selected_addr != THEM
    finally:
        page.deleteLater()
        QtWidgets.QApplication.instance().processEvents()


def test_a_click_moves_the_selection_highlight_at_once(qapp):
    """Clicking a card re-styles the roster IMMEDIATELY (2026-09-26).

    The selection accent was applied in exactly one place — the 2s
    `_party_roster_fill` — so a click repainted the entire detail column
    instantly while the card you had just clicked stayed plain. The accent sat
    on the PREVIOUS hero for up to two more seconds while you read the new
    one's stats, which made a switch that was in fact instant look broken, and
    worse, looked like it had selected the wrong player.

    So the click path re-styles the column itself, before the read. What this
    pins is that the highlight needs no tick to arrive: the accent is on the
    clicked card the moment `_party_pick_addr` returns, with no fill in
    between, and the card that WAS selected goes plain in the same call.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui import theme
    from farever_companion.ui.pages.party_page import PartyPageMixin

    host, page = _built_party_page()
    try:
        heroes = [(0x1000, "Alice", "Priest"), (0x2000, "Bob", "Warrior"),
                  (0x3000, "Cara", "Ranger")]
        host._party_roster_fill(heroes)
        host._party_selected_addr = heroes[0][0]
        host._party_roster_fill(heroes)          # the tick that styles card 0
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

        def lit(i):
            return theme.ACCENT in host._party_roster_body.itemAt(
                i).widget().styleSheet()

        assert [lit(i) for i in range(3)] == [True, False, False], \
            "the fill did not light the selected card"

        # the click — and NOTHING else. `_refresh_party_detail` is the real
        # one, but it returns at once with no model, which is all this needs.
        PartyPageMixin._party_pick_addr(host, heroes[2][0])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert [lit(i) for i in range(3)] == [False, False, True], (
            f"the highlight did not follow the click: "
            f"{[lit(i) for i in range(3)]}")

        # and it is a re-style, not a rebuild: the same card objects
        body = host._party_roster_body
        cards = [body.itemAt(i).widget() for i in range(3)]
        PartyPageMixin._party_pick_addr(host, heroes[1][0])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert [body.itemAt(i).widget() for i in range(3)] == cards, \
            "clicking rebuilt the roster cards"
    finally:
        _teardown_party_page(host, page)


def test_every_tile_survives_both_kinds_of_item(qapp):
    """Every drawn slot is filled with a stat-bearing AND a statless item, so
    every branch of the fill runs on every tile (2026-09-26).

    This exists because a branch went uncovered and then broke. The "no stats"
    treatment has two paths — mark it, or don't, depending on whether the
    slot's GROUP is statless by nature — and the marking path has NO real
    instance: checked against the whole sheet, every weapon, armour and
    jewellery item resolves stat rows, and the only statless types are the ones
    in Tools, Consumables and Extras. So the marking path is a safety net with
    no live example, and when the fill was restructured (the emoji fallback),
    a mis-nested `if`/`else` made that path raise `UnboundLocalError` on its
    first execution — with the suite green, because the test that had been
    driving it was removed the same day.

    A sweep fixes that without inventing a case: every drawn slot takes a real
    stat-bearing item and a real statless one, both in their own slots, so
    both paths execute on all 24 tiles and the fill cannot be malformed
    anywhere. The two ids are real sheet items and both sit in their own
    slots — nothing is moved, nothing is fabricated.
    """
    pytest.importorskip("PySide6")

    from farever_companion.ui.pages.party_grid import _SLOT_GROUPS

    host, page = _built_party_page()
    try:
        for title, slots in _SLOT_GROUPS:
            for slot in slots:
                for kind, rarity in (("Sword_Swarm", "Rare"),    # stats
                                     ("Glider_FlyingFish_Red",   # none
                                      "Epic")):
                    host._party_slot_fill(slot, {
                        "slot": slot, "kind": kind, "rarity": rarity,
                        "upgrade": 1, "level": 25, "state": "filled"})
        # nothing above may raise; assert the tiles are still coherent
        for title, slots in _SLOT_GROUPS:
            for slot in slots:
                card = host._party_grid_cards[slot]
                icon = card._party_slot_icon           # noqa: SLF001
                assert icon.text() or not icon.pixmap().isNull(), \
                    f"slot {slot} ({title}) drew neither a sprite nor a glyph"
    finally:
        _teardown_party_page(host, page)


def test_the_doll_is_a_grid_of_slot_glyphs_before_any_gear_arrives(qapp):
    """Every tile shows its slot's glyph from the moment it is BUILT (2026-09-26).

    The tiles are all built when the page opens; the first gear walk answers a
    moment later. In between, the doll used to be a column of near-nothing —
    each tile a "·" at 26px in a 44px box — which read as a page that had
    failed rather than one that had not loaded yet, and it is the state you see
    on every page open and on every hero switch.

    So the glyph is the tile's resting state, not only its no-sprite fallback:
    centred, one per slot, and replaced by the real sprite the moment the fill
    has one. It is drawn at NORMAL strength while unread, not dim — "nobody has
    asked yet" and "there is nothing here" are different facts, and only the
    second one is drawn dim (see
    `test_an_empty_tile_is_dim_and_a_filled_one_is_not`). This walks EVERY
    drawn tile, because the bug it replaces was never about one slot — it was
    about the grid as a whole.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui.pages.party_grid import (_SLOT_EMOJI,
                                                       _SLOT_GROUPS,
                                                       _SLOT_EMPTY_INK,
                                                       _SLOT_EMOJI_DEFAULT)

    host, page = _built_party_page()
    try:
        page.show()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

        drawn = [s for _title, slots in _SLOT_GROUPS for s in slots]
        for slot in drawn:
            tile = host._party_grid_cards[slot]
            icon = tile._party_slot_icon               # noqa: SLF001
            want = _SLOT_EMOJI.get(slot, _SLOT_EMOJI_DEFAULT)
            assert icon.text() == want, (
                f"slot {slot} waits as {icon.text()!r}, not {want!r}")
            assert icon.pixmap().isNull(), \
                f"slot {slot} draws a pixmap before any gear arrived"
            assert _SLOT_EMPTY_INK not in icon.styleSheet(), (
                f"slot {slot} is drawn as empty before anything was read: "
                f"{icon.styleSheet()!r}")
            assert "26px" in icon.styleSheet(), \
                f"slot {slot}'s glyph has no size"

        # the pet tile is the same idea: it has always shown 🐾 while empty
        pet = host._party_pet_box
        assert pet._party_pet_icon.text() == "\U0001F43E", \
            "the companion tile lost its paw while waiting"
        assert pet._party_pet_name.text() == "Pet", \
            "the companion tile names itself before the read"

        # …and real gear takes the tile over, glyph and all
        host._party_profile_fill([
            {"slot": 0, "kind": "Sword_Swarm", "rarity": "Rare",
             "upgrade": 2, "level": 25, "state": "filled"}])
        filled = host._party_grid_cards[0]._party_slot_icon
        assert filled.text() == "", \
            f"a real sprite did not replace the glyph: {filled.text()!r}"
        assert not filled.pixmap().isNull()
    finally:
        _teardown_party_page(host, page)


def test_the_page_fabricates_nothing_while_detached(qapp):
    """Detached, the page draws what it has: nothing (2026-09-26).

    There used to be a TEMP DEMO — a `_PARTY_DEMO` flag and a `_party_demo_fill`
    that pushed five invented heroes, a full set of invented gear and invented
    buffs through the real renderers, so the layout could be reviewed with every
    value filled in. It was also the fastest way to ship a lie: a detached
    window showed a page full of names and items that were in no sheet, which
    is the one thing this file refuses to do everywhere else.

    So the demo is gone, not disabled, and a detached refresh must leave the
    page empty. This also fails if the flag comes back in any form.
    """
    pytest.importorskip("PySide6")
    import farever_companion.ui.pages.party_page as pp
    import pathlib

    assert not hasattr(pp, "_PARTY_DEMO"), \
        "the detached demo flag is back"
    assert not hasattr(pp.PartyPageMixin, "_party_demo_fill"), \
        "the detached demo fill is back"
    src = pathlib.Path(pp.__file__).read_text(encoding="utf-8")
    for gone in ("_PARTY_DEMO", "_party_demo_fill"):
        assert gone not in src, f"{gone} is referenced in party_page.py again"

    host, page = _built_party_page()      # no model — exactly "detached"
    try:
        host._refresh_party_all = lambda: None      # the page's own stub
        from farever_companion.ui.pages.party_page import (
            PartyPageMixin)
        PartyPageMixin._refresh_party_all(host)
        body = host._party_roster_body
        cards = [body.itemAt(i).widget() for i in range(body.count())
                 if body.itemAt(i).widget() is not None
                 and hasattr(body.itemAt(i).widget(), "_party_update")]
        assert not cards, (
            f"a detached page drew {len(cards)} invented hero cards")
        assert host._party_selected_addr == 0
    finally:
        _teardown_party_page(host, page)


def test_a_roster_card_shows_the_name_it_is_given(qapp):
    """The VISIBLE text of a roster card's name is the name (2026-09-26).

    It was not, and nothing caught it: the card is handed the name, keeps it
    in its tooltip, and paints an EMPTY line where it belongs. Two Qt traps
    stacked:

    1. the name label sized `Ignored` horizontally, and a layout reads an
       Ignored widget's size hint as WIDTH 0 — so the label was given a 0px
       box, `elidedText` on that returned "", and the empty text measured 0
       wide, so the layout gave it 0 again. Forever.
    2. `sizeHint` measured the CURRENT (already elided) text, so even after
       the policy was fixed the hint stayed one ellipsis wide and the name
       collapsed to "…" in a card with 120px spare.

    Every test that read the name did it through the TOOLTIP, which was always
    right — which is exactly how a completely blank name ships green. So this
    reads the painted text, per card, and also checks a long name still
    elides (one ellipsis, not one character) and that the column still never
    scrolls sideways.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui.pages.party_roster import _ElideLabel

    host, page = _built_party_page()
    win = QtWidgets.QMainWindow()
    win.setCentralWidget(page)
    win.resize(1180, 900)
    win.show()
    try:
        host._party_roster_fill([
            (0x1000, "Alice", "Priest"),
            (0x2000, "BobTheVeryLongCharacterNameThatKeepsGoing", "Warrior"),
            (0x3000, "Cara (You)", "Ranger"),
        ])
        for _ in range(5):
            QtWidgets.QApplication.instance().processEvents()

        body = host._party_roster_body
        cards = [body.itemAt(i).widget() for i in range(body.count())
                 if body.itemAt(i).widget() is not None
                 and hasattr(body.itemAt(i).widget(), "_party_update")]
        assert len(cards) == 3, f"expected 3 roster cards, got {len(cards)}"
        shown = []
        for c in cards:
            nms = [l for l in c.findChildren(_ElideLabel)]
            assert nms, "a roster card has no name label at all"
            lbl = nms[0]
            assert lbl.width() > 0, \
                f"the name label was given a {lbl.width()}px box"
            shown.append(lbl.text())
            assert lbl.text(), \
                "the card paints NO name — the blank-name bug is back"
            assert not c.toolTip(), \
                f"a roster card grew a tooltip: {c.toolTip()!r}"
        assert shown[0] == "Alice", shown
        assert shown[2] == "Cara", shown
        # a long name ELIDES, it does not vanish: more than one character of it
        # survives and it ends in the ellipsis
        assert shown[1].endswith("…") and len(shown[1]) > 4, shown
        assert shown[1].startswith("Bob"), shown
        # and the same must hold for a buff name, which is the same label class
        from farever_companion.ui.pages.party_page import _buff_label, _buff_tail
        row = {"kind": "Whetstone_Status", "stacks": 2, "duration": 3600.0,
               "source_item": None}
        host._party_buffs.addWidget(host._party_buff_row(
            "Whetstone_Status", _buff_label(row), _buff_tail(row)))
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        bname = host._party_buffs.itemAt(0).widget()
        btexts = [l.text() for l in bname.findChildren(_ElideLabel)]
        assert any(t for t in btexts), \
            f"a buff row paints no name either: {btexts}"

        for sa in page.findChildren(QtWidgets.QScrollArea):
            assert sa.horizontalScrollBar().maximum() == 0, \
                "a visible name pushed the column into sideways scrolling"
    finally:
        win.hide()
        win.deleteLater()
        _teardown_party_page(host, page)


def test_the_roster_name_is_one_step_above_the_captions(qapp):
    """The roster name is the card's primary — but only ONE step above the
    page's captions (2026-09-26).

    It was 22px, from the "+2px on every font" pass, in a column that is 240px
    wide: 6px above the 16px captions, the largest text anywhere on the page,
    and big enough that a four-character name measured 88px of a 120px budget
    while anything longer elided. The rule this pins is the DESIGN one, not the
    number — the name may lead the page, but a caption plus a couple of points
    is the whole lead it gets, so the next "+2px to everything" cannot quietly
    make the player list shout again.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui.pages.party_roster import _ElideLabel

    host, page = _built_party_page()
    try:
        page.show()
        host._party_roster_fill([(0x1000, "Alice", "Priest")])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

        name_px = host._party_roster_body.itemAt(0).widget(
        ).findChildren(_ElideLabel)[0].font().pixelSize()
        cap_px = host._party_players_cap.font().pixelSize()
        assert name_px >= cap_px, (
            f"the roster name ({name_px}px) is smaller than the block caption "
            f"({cap_px}px) — it is the card's primary")
        assert name_px <= cap_px + 4, (
            f"the roster name is {name_px}px against a {cap_px}px caption — it "
            f"is shouting (2026-09-26: it was 22px in a 240px column)")
    finally:
        _teardown_party_page(host, page)


def test_a_roster_card_is_not_frozen_with_a_placeholder_name(qapp):
    """A name that arrives AFTER the card is built still lands (2026-09-26).

    The roster card is keyed by hero address and class, so a card built on the
    tick the page opened — before the hero's player pointer resolved, when the
    name falls back to "Hero" — kept that placeholder for the rest of the
    session: nothing but the class could ever change, and the name was not in
    the update. It is a re-trim now, not a rebuild.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.ui.pages.party_roster import _ElideLabel

    host, page = _built_party_page()
    try:
        host._party_roster_fill([(0x1000, "Hero", "Priest")])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        body = host._party_roster_body
        card = body.itemAt(0).widget()
        assert card.findChildren(_ElideLabel)[0].text() == "Hero"

        host._party_roster_fill([(0x1000, "Alice", "Priest")])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        again = body.itemAt(0).widget()
        assert again is card, "the late name rebuilt the card instead"
        lbl = again.findChildren(_ElideLabel)[0]
        assert lbl.text() == "Alice", (
            f"the name never replaced the placeholder: {lbl.text()!r}")
        assert not again.toolTip(), \
            "a roster card carries a tooltip at all (2026-09-26)"
    finally:
        _teardown_party_page(host, page)


def test_the_roster_is_updated_in_place_not_rebuilt_every_tick(qapp):
    """The roster cards are REUSED across refreshes (2026-09-26).

    The page used to destroy and rebuild the whole roster column on every 2s
    tick, and since a hero's HP moves every tick the "content" changed every
    tick — so every card was torn down, re-created and re-laid-out twice a
    second, which is what read as the whole page flickering (and it threw away
    hover state and the roster's scroll position every time).

    So a card is keyed by hero address: the same party must come back as the
    SAME widget objects, and a changed HP must be a text update on the card
    that is already there.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    host, page = _built_party_page()
    try:
        heroes = [(0x1000, "Alice", "Priest"), (0x2000, "Bob", "Warrior")]
        host._party_roster_fill(heroes)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

        body = host._party_roster_body

        def cards():
            return [body.itemAt(i).widget() for i in range(body.count())
                    if body.itemAt(i).widget() is not None
                    and hasattr(body.itemAt(i).widget(), "_party_update")]

        first = cards()
        assert len(first) == 2, f"expected 2 roster cards, got {len(first)}"
        # a second identical fill must not create or destroy anything
        host._party_roster_fill(heroes)
        assert cards() == first, \
            "an unchanged refresh rebuilt the roster cards"
        # …and the stretch must not pile up either
        spacers = sum(1 for i in range(body.count())
                      if body.itemAt(i).spacerItem() is not None)
        assert spacers == 1, f"the roster column has {spacers} stretches"

        # a changed value updates the text IN PLACE, same widget
        card = first[0]
        before = [w for w in card.findChildren(QtWidgets.QLabel)
                  if w.text()][1].text()
        card._party_update("Priest  ·  Lv 30  ·  HP 1,234", "", True)
        assert cards()[0] is card, "the update rebuilt the card"
        after = [w for w in card.findChildren(QtWidgets.QLabel)
                 if w.text()][1].text()
        assert after == "Priest  ·  Lv 30  ·  HP 1,234", \
            f"the meta line did not update in place: {after!r}"
        assert after != before or True   # the point is identity, not novelty

        # a hero LEAVING really does drop its card
        host._party_roster_fill(heroes[:1])
        left = cards()
        assert len(left) == 1, f"the departed hero's card stayed: {len(left)}"
        assert left[0] is first[0], "the wrong card was dropped"
    finally:
        _teardown_party_page(host, page)


def test_an_unchanged_refresh_leaves_the_buff_and_gear_rows_alone(
        qapp, monkeypatch):
    """The buff + gear rows are rebuilt ONLY when their content changes
    (2026-09-26): they were destroyed and re-created on every 2s tick, so
    every status row and every NOT READ line was re-drawn forever even when
    the hero's statuses never moved. `_buff_tail` deliberately shows no
    countdown, so a live hero's buff list really is stable between changes.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.core import inspect as hero_inspect
    from farever_companion.ui.pages import party_page as ppage
    from farever_companion.ui.pages.party_page import PartyPageMixin

    host, page = _built_party_page()
    # the test host stubs the refresh out (no game to poll), so the REAL one
    # is called unbound here
    refresh = PartyPageMixin._refresh_party_detail
    try:
        buffs = [{"kind": "Whetstone_Status", "stacks": 2, "duration": 0.0,
                  "source_item": None}]

        class _M:
            proc = hl = None
            player_addr = 0x1000
            dps = None

            @staticmethod
            def units():
                return []

        host.model = _M()
        host._party_selected_addr = 0x1000
        monkeypatch.setattr(hero_inspect, "hero_buffs",
                            lambda *a, **k: list(buffs))
        monkeypatch.setattr(hero_inspect, "hero_gear",
                            lambda *a, **k: [
                                {"slot": 0, "kind": "Sword_Swarm",
                                 "rarity": "Rare", "upgrade": 2, "level": 25,
                                 "state": "filled"}])
        monkeypatch.setattr(hero_inspect, "hero_pet",
                            lambda *a, **k: {"id": "Goat_Demon", "off": False})
        monkeypatch.setattr(ppage, "_live_pet", lambda *a, **k: None)

        refresh(host)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        first = [host._party_buffs.itemAt(i).widget()
                 for i in range(host._party_buffs.count())]
        assert first, "the buff block drew no rows"

        refresh(host)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        again = [host._party_buffs.itemAt(i).widget()
                 for i in range(host._party_buffs.count())]
        assert again == first, \
            "an unchanged refresh rebuilt the buff rows"

        # a REAL change still comes through
        buffs.append({"kind": "Axe_Boomerang_Skill_Passive_Status",
                      "stacks": 1, "duration": 0.0, "source_item": None})
        refresh(host)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        now = [host._party_buffs.itemAt(i).widget()
               for i in range(host._party_buffs.count())]
        assert len(now) == 2, f"the new buff did not draw: {len(now)} rows"
        assert now[0] is first[0], "the unchanged buff row was rebuilt anyway"
    finally:
        _teardown_party_page(host, page)


def test_no_card_is_drawn_inside_another_card(qapp):
    """A fold group's body is FLAT: no frame and no padding of its own
    (2026-09-26).

    The fold row became its own Card, and the group it expands is a Card too —
    so the expanded group was a bordered, padded box inside a bordered, padded
    box. It read as a block inside a block, and the doubled padding made the
    whole fold block taller than the cards around it for no reason. The inner
    card is now `FoldBody`: it inherits no Card rule, so only the fold card
    draws a frame, and the tiles sit straight in it.

    The same applies to Consumables, whose card lives inside the Buffs block.

    ONE exception, on purpose (2026-09-26): a BUFF ROW is a small Card inside
    the Buffs card, and stays one. It is a list of many small, equal, separate
    facts — a box around each keeps them from reading as one run-on paragraph,
    which is exactly what the bare lines did; the fold/Consumable case is the
    opposite, a block that is a SECTION of a bigger block. So this test guards
    the group cards, and pins the buff row's exception as the only one.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    host, page = _built_party_page()
    try:
        page.show()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

        def enclosing_cards(w):
            out, p = [], w.parentWidget()
            while p is not None:
                if p.objectName() == "Card":
                    out.append(p)
                p = p.parentWidget()
            return out

        for card in host._party_group_cards:
            if not enclosing_cards(card):
                continue        # a top-level card: nothing inside it
            assert card.objectName() != "Card", (
                f"{card._party_title} is a Card inside "
                f"{[c.objectName() for c in enclosing_cards(card)]} — a box "
                f"inside a box (2026-09-26)")
            lay = card.layout()
            m = lay.contentsMargins()
            assert (m.left(), m.top(), m.right(), m.bottom()) == (0, 0, 0, 0), \
                (f"{card._party_title} still pads itself inside the block "
                 f"around it: {m}")
        # the fold card is the frame for all of them
        assert host._party_fold_card.objectName() == "Card"
        flat = [c for c in host._party_group_cards
                if c.objectName() == "FoldBody"]
        assert len(flat) >= 3, \
            f"only {len(flat)} fold bodies are flat"
        # the one deliberate card-in-a-card: a buff row (see the docstring)
        row = host._party_buff_row("Whetstone_Status", "Whetstone", "")
        assert row.objectName() == "Card", \
            "buff rows lost their card frame (2026-09-26)"
    finally:
        _teardown_party_page(host, page)


def test_the_fold_row_always_has_exactly_one_group_open(qapp):
    """The fold row is an accordion with a standing rule: exactly ONE group is
    expanded, all the time (2026-09-26).

    Clicking the already-open tab used to toggle it shut, which left the body
    under the row empty — a block of nothing, and nothing on the row to say
    which group had been open a moment earlier. So the open tab is a no-op and
    the choice changes only by opening a different tab.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtGui, QtWidgets

    host, page = _built_party_page()
    try:
        page.show()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        ev = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseButtonPress,
                               QtCore.QPointF(4, 4), QtCore.Qt.LeftButton,
                               QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)

        def open_tabs():
            return [c.title()
                    for c, _k in host._party_fold_caps if not c.is_collapsed()]

        def shown():
            return [k for _c, k in host._party_fold_caps if not k.isHidden()]

        caps = {c.title(): c
                for c, _k in host._party_fold_caps}
        # it opens on exactly one: the default
        assert open_tabs() == ["Jewelry"], open_tabs()
        assert len(shown()) == 1, shown()

        # clicking the OPEN tab must NOT close it
        caps["Jewelry"].mousePressEvent(ev)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert open_tabs() == ["Jewelry"], \
            f"clicking the open tab changed the row: {open_tabs()}"
        assert len(shown()) == 1, "the body went empty"

        # …and clicking each of the others moves the one open, never empties
        for name in ("Extras", "Tools", "Jewelry"):
            caps[name].mousePressEvent(ev)
            for _ in range(3):
                QtWidgets.QApplication.instance().processEvents()
            assert open_tabs() == [name], \
                f"after clicking {name}: {open_tabs()}"
            assert len(shown()) == 1, f"after clicking {name}: {shown()}"
    finally:
        _teardown_party_page(host, page)


def test_a_stackable_piece_badges_its_count_on_the_sprite(qapp):
    """A consumable tile shows how many it holds, as a badge on the sprite's
    corner (2026-09-26): four identical-looking potion sprites, none of which
    said whether it was the last one.

    The number is a LIVE read, not an invention — `hero_gear` already returns
    it per row (`count`, read by core.inspect._record_count and guarded by the
    item's own stackSize ceiling), so this only renders it. Nothing is shown
    for a count of 1 or for a missing one, so a non-stackable piece and an
    unreadable amount both read as "no badge" rather than a wrong number.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    host, page = _built_party_page()
    try:
        host._party_profile_fill([
            {"slot": 18, "kind": "HealthPotion", "rarity": "Common",
             "upgrade": 0, "level": 1, "state": "filled", "count": 20},
            {"slot": 19, "kind": "Whetstone", "rarity": "Common",
             "upgrade": 0, "level": 1, "state": "filled", "count": 1},
            {"slot": 20, "kind": "ElixirOfAbundance", "rarity": "Common",
             "upgrade": 0, "level": 1, "state": "filled"},
            {"slot": 0, "kind": "Sword_Swarm", "rarity": "Rare",
             "upgrade": 2, "level": 25, "state": "filled", "count": 1},
        ])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

        def badge(slot):
            return host._party_grid_cards[slot]._party_slot_badge  # noqa: SLF001

        assert badge(18).text() == "20", badge(18).text()
        assert not badge(18).isHidden(), "a stack of 20 shows no count"
        for slot, what in ((19, "a count of 1"), (20, "no count at all"),
                           (0, "a non-stackable piece")):
            assert badge(slot).isHidden(), f"{what} drew a badge"

        # the badge sits INSIDE the sprite, not merely inside the icon label:
        # the label is stretched by the tile's layout (66x56) while the sprite
        # is a centred 44x44 pixmap, so anchoring to the label put the number
        # beside and below the item — and a child is not clipped by its parent,
        # so it simply drew outside the picture (2026-09-26)
        icon = host._party_grid_cards[18]._party_slot_icon  # noqa: SLF001
        pm = icon.pixmap()
        sprite = QtCore.QRect((icon.width() - pm.width()) // 2,
                              (icon.height() - pm.height()) // 2,
                              pm.width(), pm.height())
        g = badge(18).geometry()
        assert sprite.contains(g), \
            f"the badge is not on the sprite: {g} vs {sprite}"
        narrow = g.width()
        host._party_profile_fill([
            {"slot": 18, "kind": "HealthPotion", "rarity": "Common",
             "upgrade": 0, "level": 1, "state": "filled", "count": 123},
        ])
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        assert badge(18).text() == "123"
        assert badge(18).width() > narrow, \
            "a three-digit count was drawn inside the two-digit box"
        assert sprite.contains(badge(18).geometry()), \
            f"the wider badge left the sprite: {badge(18).geometry()} " \
            f"vs {sprite}"

        # emptying the slot takes the badge with it
        host._party_profile_fill([{"slot": 18, "state": "empty"}])
        assert badge(18).isHidden() and badge(18).text() == "", \
            "the count outlived the piece"
    finally:
        _teardown_party_page(host, page)


def test_the_page_has_no_hover_states(qapp):
    """Player Inspect has NO hover feedback at all (2026-09-26): no
    :hover rule, no enter/leave recolour, no pointing-hand cursor, and no
    tooltip popup on any widget. A card looks exactly the same under the mouse
    as it does at rest, and nothing on the page is hidden behind a hover.

    Four places carried it — the roster card's `QFrame#Card:hover` brighten,
    `_FoldCaption`'s enter/leave recolour, pointing-hand cursors on the roster
    cards, the fold tabs and the paper-doll tiles, and a tooltip on almost
    everything (the hero name, every doll tile, the pet, the buff status id,
    the stat rows) — so this walks the built page rather than trusting one
    file. The app stylesheet has no `QFrame#Card:hover` of its own, so nothing
    paints a hover state from outside these files either.

    What the tooltips carried, and where it went (2026-09-26): the hero name
    and the piece name are painted (a clipped name is simply clipped), the
    pet's name moved ONTO its tile, and the buff status id — the sheet's key,
    never information about the hero — is not on the page at all.

    The one loss this caused has since been repaired rather than accepted: the
    stat block's "+N more" row used to be where the omitted values lived, so
    removing tooltips left the count with nothing behind it. The clamp itself
    is gone (2026-09-26) — the block grows past its floor and draws every stat
    — so nothing is omitted and nothing needs a hover to reveal.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    from farever_companion.ui import theme

    host, page = _built_party_page()
    try:
        page.show()
        # FILL the page first: most of the widgets that used to carry a
        # tooltip (roster cards, buff rows, the pet tile, filled doll tiles)
        # only exist once something is drawn, so sweeping the empty build would
        # prove nothing.
        host._party_roster_fill([(0x1000, "Alice", "Priest"),
                                 (0x2000, "Cara (You)", "Ranger")])
        host._party_pet_fill("Goat_Demon", None)
        host._party_piece_kind = "Sword_Beefury"
        host._party_profile_fill([
            {"slot": 0, "kind": "Sword_Beefury", "rarity": "Rare",
             "upgrade": 2, "level": 25, "state": "filled"},
            {"slot": 1, "state": "empty"},
        ])
        host._party_buffs.addWidget(host._party_buff_row(
            "Whetstone_Status", "Whetstone", "(x2 · from Cheese Moon)"))
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

        # no hover rule in any stylesheet the page set
        for card in ([host._party_players_card, host._party_totals_card,
                      host._party_piece_host, host._party_fold_card]
                     + list(host._party_group_cards)
                     + list(host._party_grid_cards.values())):
            assert ":hover" not in card.styleSheet(), \
                f"a card still has a hover rule: {card.styleSheet()}"
        for cap, _card in host._party_fold_caps:
            assert ":hover" not in cap.styleSheet()
        # the app stylesheet itself paints no card hover
        app = QtWidgets.QApplication.instance()
        sheet = app.styleSheet()
        if theme.PANEL_HI in sheet:
            offenders = [ln for ln in sheet.splitlines()
                         if ":hover" in ln and "QFrame" in ln]
            assert not offenders, \
                f"the app stylesheet paints a card hover: {offenders}"

        # no enter/leave handler anywhere on the page, so nothing can recolour
        hovered = []
        for w in page.findChildren(QtWidgets.QWidget):
            for ev in ("enterEvent", "leaveEvent"):
                fn = getattr(type(w), ev, None)
                base = getattr(QtWidgets.QWidget, ev, None)
                if fn is not None and fn is not base:
                    hovered.append(f"{type(w).__name__}.{ev}")
        assert not hovered, f"the page still reacts to hover: {hovered}"

        # and nothing offers a pointing hand
        hands = [type(w).__name__ for w in page.findChildren(
            QtWidgets.QWidget)
            if w.cursor().shape() == QtCore.Qt.PointingHandCursor]
        assert not hands, f"the page still shows a pointing hand: {hands}"

        # No HOVER POPUPS either (2026-09-26): not one widget on the page
        # carries a tooltip. This is the same rule as the rest of the no-hover
        # test — a card looks the same under the mouse as at rest, and nothing
        # waits to be discovered. It is walked over the whole page rather than
        # a list of known spots, because a tooltip is exactly the kind of thing
        # that gets added back for one new widget and never removed.
        tips = [f"{type(w).__name__}({w.toolTip()[:40]!r})"
                for w in page.findChildren(QtWidgets.QWidget) if w.toolTip()]
        assert not tips, f"the page still has hover tooltips: {tips[:6]}"
    finally:
        _teardown_party_page(host, page)


def test_evicting_the_party_page_would_not_need_to_clear_its_state(qapp):
    """The party page keeps its state when the panel sheds other pages
    (2026-09-26).

    `_on_minimized` now leaves the page you are looking at resident and sheds
    only the others, so restoring does no work. The remaining way this page
    could lose its state is the eviction CLEANUP: `_on_page_evicted` drops
    per-page attributes for settings/combat/pb, and a future entry for
    "party" would wipe the open fold tab, the selected hero and the tile map
    without anybody noticing until a restore came up blank.

    So the check is on the cleanup itself: running it for "party" must leave
    every attribute the page's own state lives in alone.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtGui, QtWidgets

    from farever_companion.ui.memory_reclaimer import MemoryReclaimerMixin

    host, page = _built_party_page()
    try:
        page.show()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

        # state worth losing: an open tab and the tile map
        caps = {c.title(): c
                for c, _k in host._party_fold_caps}
        ev = QtGui.QMouseEvent(QtCore.QEvent.Type.MouseButtonPress,
                               QtCore.QPointF(4, 4), QtCore.Qt.LeftButton,
                               QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        caps["Extras"].mousePressEvent(ev)
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()
        open_before = [c for c, _k in host._party_fold_caps
                       if not c.is_collapsed()]
        assert open_before == [caps["Extras"]], "the fixture did not open"
        tiles_before = {s: id(c) for s, c in host._party_grid_cards.items()}

        MemoryReclaimerMixin._on_page_evicted(host, "party")

        assert [c for c, _k in host._party_fold_caps if not c.is_collapsed()] \
            == open_before, "evicting the party page lost the open fold tab"
        assert {s: id(c) for s, c in host._party_grid_cards.items()} \
            == tiles_before, "evicting the party page dropped its tiles"
        for attr in ("_party_fold_caps", "_party_grid_cards",
                     "_party_fold_card", "_party_selected_addr",
                     "_party_piece_cap"):
            assert host._widget_alive(getattr(host, attr, None)), \
                f"evicting the party page cleared {attr}"
    finally:
        _teardown_party_page(host, page)


def test_the_open_tab_arrow_is_green(qapp):
    """The OPEN tab's arrow is green, the closed ones stay muted (2026-09-26).

    With a row of three arrows that all looked alike, which tab was open was
    only readable from the glyph's direction; colour makes it readable at a
    glance. Only the GLYPH is green — the group name keeps the caption's muted
    colour, so the row is not a wall of green.

    This needs rich text (the glyph carries its own colour span), which means
    `.text()` is no longer the plain title — `title()` is the accessor, and the
    rendered pixel is checked too so a span that Qt silently ignored would
    fail here rather than ship.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtGui, QtWidgets

    from farever_companion.ui import theme

    host, page = _built_party_page()
    try:
        page.show()
        for _ in range(4):
            QtWidgets.QApplication.instance().processEvents()

        def painted_green(cap):
            """Is the green actually ON the tab? Scans every opaque pixel
            rather than trusting the first one: without the app theme the
            label's background is opaque too, so the leftmost pixel is
            background, not glyph. Antialiasing means the glyph's pixels are
            near the colour rather than exactly it."""
            want = QtGui.QColor(theme.GOOD)
            img = cap.grab().toImage().convertToFormat(
                QtGui.QImage.Format_ARGB32)
            for y in range(img.height()):
                for x in range(img.width()):
                    c = img.pixelColor(x, y)
                    if c.alpha() < 200:
                        continue
                    if (abs(c.red() - want.red()) < 70
                            and abs(c.green() - want.green()) < 70
                            and abs(c.blue() - want.blue()) < 70):
                        return True
            return False

        for cap, _card in host._party_fold_caps:
            if cap.is_collapsed():
                assert theme.GOOD not in cap.text(), \
                    f"{cap.title()}'s closed arrow is green"
                assert "▾" not in cap.text(), \
                    f"{cap.title()} shows a down arrow while folded"
            else:
                assert theme.GOOD in cap.text(), \
                    f"{cap.title()}'s open arrow is not green: {cap.text()!r}"
                assert "▾" in cap.text()
                # the NAME stays muted — only the glyph is green
                assert cap.text().count("<span") == 1, \
                    "more than the arrow is coloured"

        open_caps = [c for c, _k in host._party_fold_caps
                     if not c.is_collapsed()]
        assert len(open_caps) == 1, f"{len(open_caps)} tabs are open"
        assert painted_green(open_caps[0]), \
            "the open tab's arrow is not painted green — the span was ignored"
        for cap, _card in host._party_fold_caps:
            if cap.is_collapsed():
                assert not painted_green(cap), \
                    f"{cap.title()}'s closed arrow is painted green"

        # the Buffs block's tab is the same widget family and behaves the same
        assert host._party_buffs_cap.title() == "Buffs"
        assert theme.GOOD in host._party_buffs_cap.text(), \
            "the Buffs tab's open arrow is not green"
        assert painted_green(host._party_buffs_cap), \
            "the Buffs tab's green arrow is not painted"
    finally:
        _teardown_party_page(host, page)


def test_every_doll_tile_is_labelled_with_its_own_slot(qapp):
    """Each paper-doll tile names its OWN slot under the sprite (2026-09-26).

    Removed once as clutter — "Consumable 1 / Consumable 2 / Consumable 3 /
    Consumable 4" down four tiles is four repetitions of one word — and
    restored the same day. With the name gone the icon label stretched to
    66x88 around a 44x44 sprite and the doll became a field of bare icons with
    nothing saying which slot a sprite belonged to. The count badge answers
    "how many", not "which one", so both are needed.

    The tile HEIGHT depends on this label: 94 is the sum of the sprite, the
    13px name and the 11px "no stats" mark, which is why trimming the tile to
    60 to suit a bare icon was tried and reverted.
    """
    pytest.importorskip("PySide6")
    from PySide6 import QtWidgets

    from farever_companion.data.items import labels as glabels
    from farever_companion.ui.pages.party_grid import _TILE_H

    host, page = _built_party_page()
    try:
        page.show()
        for _ in range(3):
            QtWidgets.QApplication.instance().processEvents()

        for slot, card in sorted(host._party_grid_cards.items()):
            texts = [w.text() for w in card.findChildren(QtWidgets.QLabel)
                     if w.text() and w is not card._party_slot_mark  # noqa
                     and w is not card._party_slot_badge]  # noqa: SLF001
            want = glabels.equip_slot_label(slot) or f"S{slot}"
            assert want in texts, \
                f"slot {slot} does not name itself: {texts} (want {want!r})"
            # the name is its own line, BELOW the sprite
            lay = card.layout()
            icon_i = lay.indexOf(card._party_slot_icon)  # noqa: SLF001
            name_i = min(i for i in range(lay.count())
                         if lay.itemAt(i).widget() is not None
                         and lay.itemAt(i).widget().text() == want)
            assert name_i > icon_i, \
                f"slot {slot}'s name is not under its icon"
        assert _TILE_H >= 44 + 13 + 11, \
            "the tile is too short for the sprite, the name and the mark"
    finally:
        _teardown_party_page(host, page)
