"""Headless tests for the DamageDisplay damage reader (core/damage.py).

A fake HashLink heap (tests/fakemem.py) lays out ui.comp.DamageDisplay
instances pointing at st.skill.DamageResult objects exactly like the runtime
layout constants.py describes, then asserts locate -> cluster -> poll decode,
dedupe and calibration gating. No live game.
"""
from __future__ import annotations

import time

from farever_companion.constants import (
    OFF_DPS_DISPLAY_DAMAGE, OFF_DPS_RESULT_BASE_SKILL,
    OFF_DPS_RESULT_SERVER_SOURCE, OFF_DPS_RESULT_TARGET, OFF_DPS_RESULT_AMOUNT,
    OFF_DPS_RESULT_KILL, OFF_DPS_RESULT_CRITICAL, OFF_DPS_SKILL_ID,
)
from farever_companion.core.damage import DamageReader
from farever_companion.core.hl import Hl
from tests.fakemem import FakeProc, HeapBuilder

DISP = "ui.comp.DamageDisplay"
RES = "st.skill.DamageResult"
BSKILL = "st.skill.BaseSkill"
HERO = "ent.Hero"
FOE = "ent.Foe"


def _world():
    """FakeProc with one DamageDisplay + DamageResult + hero/foe/skill."""
    proc = FakeProc()
    b = HeapBuilder(proc)
    hero = b.make_instance(HERO, size=0x100)
    foe = b.make_instance(FOE, size=0x100)

    bskill = b.make_instance(BSKILL, size=0x100)
    proc.put_u64(bskill + OFF_DPS_SKILL_ID, b.make_string("Sword_Base_Attack"))

    res = b.make_instance(RES, size=0x100)
    proc.put_u64(res + OFF_DPS_RESULT_BASE_SKILL, bskill)
    proc.put_u64(res + OFF_DPS_RESULT_SERVER_SOURCE, hero)
    proc.put_u64(res + OFF_DPS_RESULT_TARGET, foe)
    proc.put_f64(res + OFF_DPS_RESULT_AMOUNT, 1234.5)
    proc.write(res + OFF_DPS_RESULT_KILL, b"\x01")
    proc.write(res + OFF_DPS_RESULT_CRITICAL, b"\x01")

    disp = b.make_instance(DISP, size=0x500)
    proc.put_u64(disp + OFF_DPS_DISPLAY_DAMAGE, res)
    return proc, b, {"hero": hero, "foe": foe, "res": res, "disp": disp}


def _reader(proc):
    return DamageReader(proc, Hl(proc), my_hero=None)


def test_locate_and_poll_decode():
    proc, b, addrs = _world()
    r = _reader(proc)
    tp = r.find_type()
    assert tp is not None
    assert r.hl.type_name(tp) == DISP

    assert r.refresh_ranges() >= 1
    assert r._ranges_ready
    events = r.poll()
    assert len(events) == 1
    ev = events[0]
    assert abs(ev.amount - 1234.5) < 1e-6
    assert ev.skill == "Sword_Base_Attack"
    assert ev.crit is True
    assert ev.kill is True
    assert ev.source_addr == addrs["hero"]
    assert ev.target_addr == addrs["foe"]
    assert ev.incoming is False


def test_poll_dedupes_until_number_changes():
    proc, b, addrs = _world()
    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    assert len(r.poll()) == 1
    # same content again -> no duplicate event
    assert r.poll() == []

    # a NEW hit reuses the same result object with a new amount
    proc.put_f64(addrs["res"] + OFF_DPS_RESULT_AMOUNT, 999.0)
    evs = r.poll()
    assert len(evs) == 1
    assert abs(evs[0].amount - 999.0) < 1e-6


def test_calibration_gate_blocks_poll_without_offsets(monkeypatch):
    import farever_companion.core.damage as dm
    proc, b, _ = _world()
    r = _reader(proc)
    monkeypatch.setattr(dm, "OFF_DPS_DISPLAY_DAMAGE", None)
    assert DamageReader.calibrated() is False
    assert r.find_type() is not None          # type locate is independent
    assert r.refresh_ranges() == 0
    assert r.poll() == []


def test_incoming_tag_when_target_is_my_hero():
    proc, b, addrs = _world()
    # retarget the result at my own hero -> incoming
    proc.put_u64(addrs["res"] + OFF_DPS_RESULT_TARGET, addrs["hero"])
    r = DamageReader(proc, Hl(proc), my_hero=addrs["hero"])
    assert r.find_type() is not None
    r.refresh_ranges()
    evs = r.poll()
    assert len(evs) == 1 and evs[0].incoming is True


def test_type_hint_env_skips_scan(monkeypatch):
    proc, b, addrs = _world()
    tp = b.types[DISP]
    monkeypatch.setenv("FAREVER_DMG_TYPE", hex(tp))
    r = _reader(proc)
    assert r.find_type() == tp
    # hint wins; scanning finds the same type either way
    assert r.hl.type_name(r._type_ptr) == DISP


# --- reflection-first resolution ------------------------------------------
# Real builds drift from the constants; the reader must resolve every field by
# name through the runtime's own field table when the type carries one. The
# HeapBuilder layout lets field offsets differ from constants.py entirely and
# the decode must still come out right (and report used_refl in diagnostics).

def test_reflection_first_resolves_field_offsets():
    proc = FakeProc()
    b = HeapBuilder(proc)
    hero = b.make_instance(HERO, size=0x100)
    foe = b.make_instance(FOE, size=0x100)

    # each type gets its OWN drifted layout (starts at 8, strictly ascending)
    b.make_type(BSKILL, fields={"kind": 8, "other": 0x10})   # kind @ 8
    b.make_type(RES, fields={
        "baseSkill": 8, "serverSource": 0x28, "target": 0x30,
        "amount": 0x50, "kill": 0x60, "critical": 0x61,
    })
    b.make_type(DISP, fields={"dmg": 8})

    bskill = b.make_instance(BSKILL, size=0x80)
    proc.put_u64(bskill + 8, b.make_string("Sword_Base_Attack"))
    res = b.make_instance(RES, size=0x100)
    proc.put_u64(res + 8, bskill)
    proc.put_u64(res + 0x28, hero)
    proc.put_u64(res + 0x30, foe)
    proc.put_f64(res + 0x50, 888.0)
    proc.write(res + 0x60, b"\x01")     # kill
    proc.write(res + 0x61, b"\x00")     # critical: off
    disp = b.make_instance(DISP, size=0x40)
    proc.put_u64(disp + 8, res)

    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    evs = r.poll()
    assert len(evs) == 1
    ev = evs[0]
    assert abs(ev.amount - 888.0) < 1e-6      # amount @ 0x50, not OFF_* const
    assert ev.skill == "Sword_Base_Attack"    # kind @ 8, not OFF_DPS_SKILL_ID
    assert ev.kill is True and ev.crit is False
    assert ev.source_addr == hero and ev.target_addr == foe
    diag = r._diag
    assert diag["ok"] == 1
    assert diag["used_refl"] is True


def test_reflection_wins_over_constants_when_both_exist():
    proc = FakeProc()
    b = HeapBuilder(proc)
    hero = b.make_instance(HERO, size=0x100)
    foe = b.make_instance(FOE, size=0x100)
    b.make_type(BSKILL, fields={"kind": 8})
    b.make_type(RES, fields={
        "baseSkill": 8, "serverSource": 0x28, "amount": 0x50,
        "kill": 0x60, "critical": 0x61,
    })
    b.make_type(DISP, fields={"dmg": 8})
    bskill = b.make_instance(BSKILL, size=0x80)
    proc.put_u64(bskill, b.types[BSKILL])
    proc.put_u64(bskill + 8, b.make_string("Fireball"))
    res = b.make_instance(RES, size=0x100)
    proc.put_u64(res + 8, bskill)
    proc.put_u64(res + 0x28, hero)
    proc.put_f64(res + 0x50, 555.0)
    disp = b.make_instance(DISP, size=0x40)
    proc.put_u64(disp + 8, res)

    # plant decoy data at the CONSTANT offsets too - if the reader preferred
    # constants, it would read these and fail; reflection must win outright
    proc.put_f64(res + OFF_DPS_RESULT_AMOUNT, 1e9)      # decoy (reflect:
                                                        # amount is @0x50)
    proc.put_u64(res + OFF_DPS_RESULT_SERVER_SOURCE, 0)  # decoy

    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    evs = r.poll()
    assert len(evs) == 1
    assert abs(evs[0].amount - 555.0) < 1e-6
    assert evs[0].skill == "Fireball"


# --- stale-build diagnostics ------------------------------------------------
# A reader that finds displays but decodes nothing must say WHY (per-reason
# drop counts), so a stale build is evidence, not a silent zero.

def test_silent_poll_reports_no_res():
    proc, b, addrs = _world()
    # display field points at a non-pointer (freed/recycled or stale build)
    proc.put_u64(addrs["disp"] + OFF_DPS_DISPLAY_DAMAGE, 0)
    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    assert r.poll() == []
    d = r._diag
    assert d["displays"] == 1 and d["no_res"] == 1 and d["ok"] == 0


def test_silent_poll_reports_bad_amount():
    proc, b, addrs = _world()
    # result present but the amount slot reads zero -> bad_amount, not ok
    proc.put_f64(addrs["res"] + OFF_DPS_RESULT_AMOUNT, 0.0)
    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    assert r.poll() == []
    d = r._diag
    assert d["res"] == 1 and d["bad_amount"] == 1 and d["ok"] == 0


def test_locate_finds_type_in_read_only_module_image():
    """Regression for the live bug documented in the DPS3 handoff: on this
    runtime the type objects and their name pointers live in the READ-ONLY
    module image, so a writable-only back-reference scan (rw_only=True)
    finds zero refs and locate never completes. Every locate pass must scan
    rw_only=False; only the GC-heap instance hunt stays writable-only."""
    proc = FakeProc()
    # read-only "module image": allocate the type objects, then flip the
    # region's writable flag off (reads still work, rw_only scans skip it)
    ro = HeapBuilder(proc, base=0x3000000, region=0x40000)
    for cls in (DISP, RES, BSKILL, HERO, FOE):
        ro.make_type(cls)
    for i, (b, s, p, w) in enumerate(proc._regions):
        if b == 0x3000000:
            proc._regions[i] = (b, s, p, False)

    # writable GC heap: instance blocks point at the read-only type objects
    rw = HeapBuilder(proc, base=0x100000, region=0x200000)
    rw.types = ro.types
    rw.type_objs = ro.type_objs
    hero = rw.make_instance(HERO, size=0x100)
    foe = rw.make_instance(FOE, size=0x100)
    bskill = rw.make_instance(BSKILL, size=0x100)
    proc.put_u64(bskill + OFF_DPS_SKILL_ID, rw.make_string("Sword_Base_Attack"))
    res = rw.make_instance(RES, size=0x100)
    proc.put_u64(res + OFF_DPS_RESULT_BASE_SKILL, bskill)
    proc.put_u64(res + OFF_DPS_RESULT_SERVER_SOURCE, hero)
    proc.put_u64(res + OFF_DPS_RESULT_TARGET, foe)
    proc.put_f64(res + OFF_DPS_RESULT_AMOUNT, 4321.0)
    disp = rw.make_instance(DISP, size=0x500)
    proc.put_u64(disp + OFF_DPS_DISPLAY_DAMAGE, res)

    r = DamageReader(proc, Hl(proc))
    tp = r.find_type()
    assert tp is not None                      # read-only image refs resolved
    assert r.hl.type_name(tp) == DISP
    assert r.refresh_ranges() >= 1             # writable GC heap instance hunt
    evs = r.poll()
    assert len(evs) == 1 and abs(evs[0].amount - 4321.0) < 1e-6


# --- header-verified payload reads -----------------------------------------
# A display's `dmg` slot is only trustworthy when the object behind it IS an
# st.skill.DamageResult (verified by its hl_type header). Pooled/freed floaty
# displays point at reused objects - we caught a live $DebugDay ref - so an
# unverified payload must never decode into an event.

def test_payload_with_wrong_header_is_rejected():
    proc, b, addrs = _world()
    # the display now points at a *different* live class (pooled-slot reuse)
    junk = b.make_instance("ui.win.dev.$DebugDay", size=0x40)
    proc.put_u64(addrs["disp"] + OFF_DPS_DISPLAY_DAMAGE, junk)
    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    assert r.poll() == []
    d = r._diag
    assert d["res"] == 1 and d["bad_hdr"] == 1 and d["ok"] == 0
    assert d["no_res"] == 0


def test_junk_named_payload_rejected_even_with_layout():
    proc, b, addrs = _world()
    # same field layout but a NON-combat class name -> never decodes (the
    # display's dmg slot can point at any pooled object: ui.*/$ classes are
    # not combat results no matter what bytes they carry)
    junk = b.make_instance("ui.win.dev.$DebugDay", size=0x100)
    proc.put_u64(junk + OFF_DPS_RESULT_AMOUNT, 0x3FE4B0)
    proc.put_u64(addrs["disp"] + OFF_DPS_DISPLAY_DAMAGE, junk)
    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    assert r.poll() == []
    assert r._diag["bad_hdr"] == 1


def test_sibling_heal_result_class_is_adopted():
    """Heal floaties may not share the DamageResult class; a structurally
    identical sibling (st.skill.HealResult) must be adopted on first sight
    and decode with the same field offsets."""
    proc, b, addrs = _world()
    hero2 = b.make_instance(HERO, size=0x100)
    heal = b.make_instance("st.skill.HealResult", size=0x100)
    proc.put_u64(heal + OFF_DPS_RESULT_BASE_SKILL,
                 b.make_instance(BSKILL, size=0x80))
    proc.put_u64(heal + OFF_DPS_RESULT_SERVER_SOURCE, addrs["hero"])
    proc.put_u64(heal + OFF_DPS_RESULT_TARGET, hero2)
    proc.put_f64(heal + OFF_DPS_RESULT_AMOUNT, 250.0)   # a heal amount
    proc.put_u64(addrs["disp"] + OFF_DPS_DISPLAY_DAMAGE, heal)

    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    evs = r.poll()
    assert len(evs) == 1
    ev = evs[0]
    assert abs(ev.amount - 250.0) < 1e-6
    assert ev.target_addr == hero2 and ev.source_addr == addrs["hero"]
    # the class got adopted (cached), and the same content dedupes
    assert any("HealResult" in nm for nm in r._payload_types.values())
    assert r.poll() == []


# --- caster attribution via the baseSkill object ----------------------------
# Live results frequently ship serverSource == 0 (we verified on the running
# game); the caster rides on the skill object instead (st.skill.Skill.owner).

def test_serverSource_empty_falls_back_to_skill_owner():
    proc = FakeProc()
    b = HeapBuilder(proc)
    hero = b.make_instance(HERO, size=0x100)
    foe = b.make_instance(FOE, size=0x100)
    b.make_type(BSKILL, fields={"kind": 8, "owner": 0x18})
    bskill = b.make_instance(BSKILL, size=0x80)
    proc.put_u64(bskill + 8, b.make_string("Sword_Base_Attack"))
    proc.put_u64(bskill + 0x18, hero)              # skill.owner = the caster

    res = b.make_instance(RES, size=0x100)
    proc.put_u64(res + OFF_DPS_RESULT_BASE_SKILL, bskill)
    proc.put_u64(res + OFF_DPS_RESULT_SERVER_SOURCE, 0)   # empty on this build
    proc.put_u64(res + OFF_DPS_RESULT_TARGET, foe)
    proc.put_f64(res + OFF_DPS_RESULT_AMOUNT, 321.0)
    disp = b.make_instance(DISP, size=0x500)
    proc.put_u64(disp + OFF_DPS_DISPLAY_DAMAGE, res)

    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    evs = r.poll()
    assert len(evs) == 1
    assert evs[0].source_addr == hero              # caster came from skill.owner
    assert evs[0].target_addr == foe
    assert abs(evs[0].amount - 321.0) < 1e-6


# --- skill-name chain drift is diagnostic, never fatal ----------------------
# A header-verified result is real combat even when the name chain drifted; it
# decodes with skill "?" and counts toward bad_skill for calibration.

def test_unreadable_skill_still_yields_event():
    proc = FakeProc()
    b = HeapBuilder(proc)
    hero = b.make_instance(HERO, size=0x100)
    foe = b.make_instance(FOE, size=0x100)
    b.make_type(BSKILL, fields={"kind": 8})        # kind field exists...
    bskill = b.make_instance(BSKILL, size=0x80)
    proc.put_u64(bskill + 8, 0)                    # ...but holds no String

    res = b.make_instance(RES, size=0x100)
    proc.put_u64(res + OFF_DPS_RESULT_BASE_SKILL, bskill)
    proc.put_u64(res + OFF_DPS_RESULT_SERVER_SOURCE, hero)
    proc.put_u64(res + OFF_DPS_RESULT_TARGET, foe)
    proc.put_f64(res + OFF_DPS_RESULT_AMOUNT, 55.0)
    disp = b.make_instance(DISP, size=0x500)
    proc.put_u64(disp + OFF_DPS_DISPLAY_DAMAGE, res)

    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    evs = r.poll()
    assert len(evs) == 1                           # event flows despite "?"
    assert evs[0].skill == "?"
    assert r._diag["bad_skill"] >= 1               # ...and it's flagged
    assert r._diag["ok"] == 1


def test_result_type_hint_env_skips_scan(monkeypatch):
    proc, b, addrs = _world()
    rt = b.types[RES]
    monkeypatch.setenv("FAREVER_DMG_RESULT_TYPE", hex(rt))
    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    evs = r.poll()
    assert len(evs) == 1 and abs(evs[0].amount - 1234.5) < 1e-6
    # hint used, verified by name, no full scan needed
    assert r._result_type_ptr == rt
    assert r.hl.type_name(r._result_type_ptr) == RES


# --- skill-name decode prefers header-verified Strings ---------------------
# hl_string trusts any object shaped like a String (+8 ptr, +0x10 length), so
# a wrong offset on another player's skill object can decode arbitrary memory
# as printable text. The reader must prefer candidates whose type header is a
# real String (type name ends in "String").

def test_skill_name_prefers_verified_string_over_garbage_lookalike():
    proc = FakeProc()
    b = HeapBuilder(proc)
    hero = b.make_instance(HERO, size=0x100)
    foe = b.make_instance(FOE, size=0x100)
    b.make_type(BSKILL, fields={"kind": 8, "name": 0x20})
    bskill = b.make_instance(BSKILL, size=0x80)

    # kind slot (first candidate) -> a NON-String object that hl_string would
    # happily "decode" as printable text if the reader didn't verify headers
    decoy = b.make_instance("st.skill.Status", size=0x20)
    garb = b.alloc(64)
    proc.put_utf16(garb, "RandomGodText")
    proc.put_u64(decoy + 8, garb)
    proc.put_i32(decoy + 0x10, len("RandomGodText"))
    proc.put_u64(bskill + 8, decoy)
    # name slot -> a real hashlink String
    proc.put_u64(bskill + 0x20, b.make_string("Sword_Base_Attack"))

    res = b.make_instance(RES, size=0x100)
    proc.put_u64(res + OFF_DPS_RESULT_BASE_SKILL, bskill)
    proc.put_u64(res + OFF_DPS_RESULT_SERVER_SOURCE, hero)
    proc.put_u64(res + OFF_DPS_RESULT_TARGET, foe)
    proc.put_f64(res + OFF_DPS_RESULT_AMOUNT, 77.0)
    disp = b.make_instance(DISP, size=0x500)
    proc.put_u64(disp + OFF_DPS_DISPLAY_DAMAGE, res)

    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    evs = r.poll()
    assert len(evs) == 1
    assert evs[0].skill == "Sword_Base_Attack"   # not "RandomGodText"


def test_skill_name_lenient_fallback_when_no_verified_string():
    # Builds whose String class name isn't recognized keep the old lenient
    # decode — a known-good name must never regress to "?".
    proc = FakeProc()
    b = HeapBuilder(proc)
    hero = b.make_instance(HERO, size=0x100)
    foe = b.make_instance(FOE, size=0x100)
    b.make_type(BSKILL, fields={"kind": 8})
    bskill = b.make_instance(BSKILL, size=0x80)
    garb = b.alloc(64)
    proc.put_utf16(garb, "OldStyleName")
    lookalike = b.make_instance("st.skill.Whatever", size=0x20)
    proc.put_u64(lookalike + 8, garb)
    proc.put_i32(lookalike + 0x10, len("OldStyleName"))
    proc.put_u64(bskill + 8, lookalike)

    res = b.make_instance(RES, size=0x100)
    proc.put_u64(res + OFF_DPS_RESULT_BASE_SKILL, bskill)
    proc.put_u64(res + OFF_DPS_RESULT_SERVER_SOURCE, hero)
    proc.put_u64(res + OFF_DPS_RESULT_TARGET, foe)
    proc.put_f64(res + OFF_DPS_RESULT_AMOUNT, 12.0)
    disp = b.make_instance(DISP, size=0x500)
    proc.put_u64(disp + OFF_DPS_DISPLAY_DAMAGE, res)

    r = _reader(proc)
    assert r.find_type() is not None
    r.refresh_ranges()
    evs = r.poll()
    assert len(evs) == 1
    assert evs[0].skill == "OldStyleName"
