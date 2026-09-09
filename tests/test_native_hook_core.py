"""The native bridge has exactly ONE hook core (2026-09-29).

The four detours, their helpers and the struct offsets used to exist TWICE - once
inline in hook/farever_dps/farever_dps.c and again in hook/proxy_bridge.h - and
the two copies drifted apart for as long as they both existed. Nothing failed
loudly; the visible damage was that st.skill.DamageResult.affinity got added to
the injector only, so proxy capture kept emitting untagged hits. The shipped-DLL
tests in test_dps_capture.py could not see that, because they read build output:
a re-duplicated core would simply be rebuilt and keep passing.

So the invariant is pinned at the SOURCE level, in the sibling GameFiles tree,
where a second copy is the failure mode. These tests skip when that tree is not
checked out (CI and most worktrees) rather than failing on its absence.
"""
import hashlib
import importlib.util
import pathlib
import re
import sys

import pytest

from tests import source_guard

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_HOOKS = _ROOT.parent / "GameFiles" / "Farever" / "hooks" / "hook"

pytestmark = pytest.mark.skipif(
    not _HOOKS.is_dir(), reason="sibling GameFiles native tree is not checked out")

_CORE = _HOOKS / "bridge_core.h"
_INJECTOR = _HOOKS / "farever_dps" / "farever_dps.c"
_PROXY = _HOOKS / "proxy_bridge.h"
# GENERATED: build_all.bat concatenates the injector with safe_unload.c, so it
# repeats everything the injector says and is checked as a concat, not as source.
_CONCAT = _HOOKS / "farever_dps" / "farever_dps_full.c"

_HOSTS = (("the proxy host", _PROXY), ("the injector host", _INJECTOR))

# Everything that was duplicated and must now be defined in the core alone.
_SHARED = (
    "is_valid_ptr", "mem_readable", "ensure_executable", "bridge_try_arm_hook",
    "read_hl_string", "is_hero_instance", "resolve_hero_info", "resolve_unit_name",
    "read_affinity_tag", "send_hit_event", "init_network", "send_event",
    "log_msg", "send_status", "bridge_arm_all_hooks", "bridge_heartbeat_loop",
    "detour_onInflictDamage", "detour_playHitHealFX", "detour_set_health",
    "detour_onDie", "detour_unitDied", "detour_canary", "bridge_arm_canary",
    "bridge_canary_tick", "note_hit_event",
)

_TYPES = frozenset("""void bool int char double float unsigned signed long short
    size_t uintptr_t uint8_t uint32_t MH_STATUS HANDLE HMODULE SOCKET BOOL DWORD
    wchar_t LPVOID""".split())
_QUALIFIERS = frozenset("static inline const extern".split())
_CONTROL = frozenset("if else while for return switch do case break continue goto".split())


def _text(path):
    # Through the source guard, because every path this reads lives OUTSIDE the
    # test's own tree: the sibling GameFiles sources and the generated manifest.
    # Another agent or an editor rewriting one of them mid-session used to
    # surface as a bare assertion in a test that looks like it is checking the
    # manifest - see tests/source_guard.py.
    return source_guard.read_text(path, encoding="utf-8", errors="replace")


def _definitions(text, name):
    """The declarations of `name` in `text` - not its calls.

    A definition's line begins with the return type, so a guard like
    `if (!is_valid_ptr(p)) {` and a call like `log_msg("...")` are excluded by
    the prefix, and a body is required (a `{` before the next `;`), which is
    also what allows a signature wrapped over several lines.

    Per-table detours are macro-generated (`detour_x_T##N`), so the pattern
    accepts that one suffix too: the base name never appears followed by `(`
    on its own, and counting the macro body as the single definition is what
    keeps this test meaningful for them (it went blind - two false failures -
    when the wrappers moved into DEFINE_*_WRAPPER macros).
    """
    found = []
    for m in re.finditer(r"\b%s(?:_T##N)?\s*\(" % re.escape(name), text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        before = text[line_start:m.start()].strip()
        if not before:
            continue
        first = before.split()[0]
        if first in _CONTROL:
            continue
        if not (first in _TYPES or first in _QUALIFIERS):
            continue
        tail = text[m.end():m.end() + 400]
        brace, semi = tail.find("{"), tail.find(";")
        if brace == -1 or (semi != -1 and semi < brace):
            continue
        found.append(f"{before} {name}")
    return found


def _live_core():
    """The core with its opt-in #if block removed, i.e. what actually compiles."""
    return re.sub(r"(?s)#if BRIDGE_HP_DIFF_FALLBACK.*?#endif", "", _text(_CORE))


def test_both_hosts_build_from_the_one_core():
    """The core exists and each host includes it, by its own relative path."""
    assert _CORE.is_file(), "hook/bridge_core.h is the single shared hook core"
    assert '#include "bridge_core.h"' in _text(_PROXY)
    assert '#include "../bridge_core.h"' in _text(_INJECTOR)


@pytest.mark.parametrize("name", _SHARED)
def test_the_core_defines_each_shared_symbol_once(name):
    assert len(_definitions(_text(_CORE), name)) == 1, f"{name} in bridge_core.h"


@pytest.mark.parametrize("label,path", _HOSTS)
@pytest.mark.parametrize("name", _SHARED)
def test_no_host_redefines_a_symbol_the_core_owns(label, path, name):
    """A host may CALL these; a second definition is the bug this file guards."""
    found = _definitions(_text(path), name)
    assert not found, f"{label} re-defined {name}: {found[:1]}"


def test_struct_offsets_are_declared_in_one_place():
    """The specific regression: OFF_DR_AFFINITY reached the injector only.

    Checked tree-wide by NAME, because a duplicated offset block is exactly how
    a field added on one path stays invisible on the other.
    """
    sources = [p for p in _HOOKS.rglob("*") if p.suffix in (".c", ".h")]
    sources = [p for p in sources
               if p.name not in ("farever_dps_full.c", "selftest_sha256.c")]
    counts = {}
    for path in sources:
        for name in re.findall(r"(?m)^#\s*define\s+(OFF_[A-Z0-9_]+)", _text(path)):
            counts.setdefault(name, []).append(path.name)

    duplicated = {n: sorted(set(f)) for n, f in counts.items() if len(set(f)) > 1}
    assert not duplicated, f"struct offsets declared in more than one file: {duplicated}"
    # Since 2026-10-02 the single home is bridge_offsets.h, GENERATED from
    # farever_companion/constants.py. The intent of this pin is unchanged (one
    # place, so a field added on one path cannot be invisible on the other) -
    # only the filename moved, and moving it is what let the offset calibrator
    # see these numbers at all. See tests/test_generated_bridge_offsets.py.
    assert counts.get("OFF_DR_AFFINITY") == ["bridge_offsets.h"]
    assert counts.get("OFF_UNITID") == ["bridge_offsets.h"], (
        "every compiled offset must live in the generated header, never in a "
        "hand-written .c/.h the calibrator cannot update")


def test_the_hit_payload_is_built_in_one_place():
    """One builder, so a new field cannot reach the outgoing template and miss
    the incoming one - the way `aff` would have been half-shipped a second time.
    """
    live = _live_core()
    assert live.count('\\"t\\":\\"hit\\"') == 2, "one hit template per branch"
    assert live.count('\\"aff\\":\\"%ls\\"') == 2, "both branches carry the type"
    # The target's address splits a multi-dummy yard per dummy; a name cannot
    # tell two training dummies apart, so both templates must carry it too.
    assert live.count('\\"taddr\\":%llu') == 2, "both branches carry the address"
    for label, path in _HOSTS:
        assert '\\"t\\":\\"hit\\"' not in _text(path), f"{label} builds its own hit payload"
        assert '\\"aff\\"' not in _text(path), f"{label} builds its own hit payload"


def test_the_hp_diff_fallback_is_off_and_tags_its_own_hit():
    """The injector's synthetic HP-diff hit is the one core behaviour the proxies
    never had. It is compiled out by default (it double-counts a real hit that
    onInflictDamage already reported), and if switched on it must still send the
    affinity key rather than omit it.
    """
    core = _text(_CORE)
    assert re.search(r"(?m)^#\s*define\s+BRIDGE_HP_DIFF_FALLBACK\s+0\s*$", core)
    block = re.search(r"(?s)#if BRIDGE_HP_DIFF_FALLBACK(.*?)#endif", core)
    assert block, "the fallback block is gone; retire the switch with it"
    assert '\\"aff\\":\\"\\"' in block.group(1)


def test_the_concatenated_dll_source_is_its_two_halves():
    """build_all.bat makes farever_dps_full.c with `copy /b`, not by hand."""
    halves = (_HOOKS / "farever_dps" / "farever_dps.c").read_bytes() + \
             (_HOOKS / "farever_dps" / "safe_unload.c").read_bytes()
    assert _CONCAT.read_bytes() == halves


def test_the_sha256_selftest_does_not_pull_in_the_core():
    """selftest_sha256.c includes hlboot_verify.h with its OWN stub primitives.

    hlboot_verify.h must keep requiring, not providing, them - if it ever
    included bridge_core.h the selftest would stop compiling.
    """
    assert "bridge_core.h" not in _text(_HOOKS / "hlboot_verify.h")


def _verify_code():
    """hlboot_verify.h with its comments stripped, so prose about a bug cannot
    be mistaken for the bug."""
    text = _text(_HOOKS / "hlboot_verify.h")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def test_no_anchor_index_is_ever_used_as_a_displacement():
    """An ANCHOR_* index may be a PREDICATE and nothing else.

    2026-09-30: a game patch moved fmt.jpg_decode 34203 -> 34743 and
    fmt.inflate_init 36225 -> 36702, nothing regenerated the two constants, and
    the heap sweep derived its candidate as `slot - ANCHOR_*`. That does not
    fail to find anything - it silently returns a base shifted by exactly the
    distance the index moved (540 slots), and because jpg/png are adjacent and
    moved together, two of the four anchors still lined up there. The bridge
    armed four live detours on the wrong functions, reported READY, and
    captured nothing.

    The base is measured from the heap now (one allocation, every slot a code
    pointer, so its extent is a run hlb_run_bounds' walk can find), which means
    an index has no arithmetic role left to lose. This pins that, so the
    subtraction cannot come back as a "small" fix.
    """
    code = _verify_code()
    legal = re.findall(r"table\[ANCHOR_[A-Z0-9_]+\]", code)
    assert len(legal) == 4, "the four anchors are asked about a candidate base"
    # Whatever an anchor is used for, it can only be that lookup - so a
    # displacement (`slot - ANCHOR_X`), a step (`ANCHOR_X + 1`) or a comparison
    # against anything but the live address all fail here at once.
    rest = re.sub(r"table\[ANCHOR_[A-Z0-9_]+\]", "", code)
    offenders = [ln.strip() for ln in rest.splitlines()
                 if "ANCHOR_" in ln and not ln.lstrip().startswith("#")]
    assert not offenders, f"an anchor index is doing more than indexing: {offenders}"


def test_no_table_is_armed_on_one_discovery_route_alone():
    """A base has to be produced by BOTH routes, and the two routes are:
    the statics walk (pointers out of module static data) and the heap sweep (a
    run of code pointers exactly FARVER_DPS_TABLE_ENTRIES long). Whichever one
    proposes a base, the other has to confirm that same address before any hook
    is installed - so no call site may hand a table-shaped base straight to the
    arming path.

    This is the other half of the stale-anchor story: the anchor predicate makes
    a WRONG table hard to accept, and corroboration makes a LONE witness hard to
    accept. A decoy duplicate that passes the anchors but that the runtime's own
    pointer graph does not reach used to be armed "and let the live one speak".
    """
    code = _verify_code()
    calls = [m for m in re.finditer(r"is_candidate_table\(", code)
             if not code[:m.start()].rstrip().endswith("bool")]
    assert len(calls) == 2, (
        "one call site per route: the statics walk and the heap sweep")
    for m in calls:
        assert "hlb_corroborate(" in code[m.end():m.end() + 260], (
            "a table-shaped base must reach the shared corroboration gate")
    for helper in ("hlb_pointers_accept", "hlb_statics_scan",
                   "hlb_base_is_structural", "hlb_corroborate"):
        assert len(_definitions(code, helper)) == 1, f"{helper} is defined once"


def test_both_routes_share_one_traversal_and_one_verdict():
    """The corroboration has to be the route's OWN method, not a lookalike.

    The statics walk is one traversal with two questions (find a table / reach
    this address), so a witness cannot succeed where the route it checks would
    fail; and a lone witness refuses loudly instead of arming quietly.
    """
    code = _verify_code()
    # The section walk lives in exactly one place.
    assert code.count("IMAGE_SCN_MEM_WRITE") == 1
    assert code.count('GetModuleHandleA("libhl.dll")') == 1
    # ... and both callers reach it: the hunt, and the corroboration of a base
    # the heap route found.
    assert code.count("hlb_statics_scan(") == 3, "definition + witness + hunt"
    assert "hlb_statics_scan(deadline, NULL, &cand" in code
    assert "hlb_statics_scan(deadline, base, &found" in code
    # The gate itself consults both routes' methods - one for a base the
    # pointers found, one for a base the heap found.
    # `\\) {` skips the forward declaration, which ends in `);`.
    # rindex: the definition is the LAST thing with this signature (an earlier
    # one is the forward declaration the helper below it needs).
    sig = "static bool hlb_corroborate(uintptr_t *base, int route, uint64_t deadline,"
    start = code.rindex(sig)
    gate = code[start:code.index("\n}", start)]
    assert "allow_uncorroborated_arm" in gate, "hlb_corroborate is the gate"
    assert "hlb_base_is_structural(base)" in gate, "the heap witness is consulted"
    assert "hlb_statics_scan(deadline, base, &found" in gate, (
        "the pointer witness is consulted")
    # A refusal is loud, and it is a status line the companion shows - not a log
    # line nobody opens.
    refuse = re.search(r"(?s)static void hlb_refuse_arm\(.*?^}", code, re.M)
    assert refuse, "hlb_refuse_arm is the single refusal path"
    assert 'send_status("hunt", msg, 0)' in refuse.group(0)
    assert "NOT ARMING" in refuse.group(0)
    assert "both must agree" in refuse.group(0)
    # The wire message stays short enough for the UDP status wrapper to keep the
    # whole sentence (the file log carries the long form).
    wire = re.search(r"snprintf\(msg, sizeof\(msg\),(.*?)\);", refuse.group(0), re.S)
    assert wire and len(wire.group(1)) < 200, "the status line is not a paragraph"


def test_the_single_route_escape_hatch_is_env_gated_and_logged():
    """One route alone is a dev-only A/B, not a mode anyone can drift into:
    the override has to be set on the game process and it says so in the log."""
    code = _verify_code()
    gate = re.search(r"(?s)static bool allow_uncorroborated_arm\(.*?^}", code, re.M)
    assert gate, "the override is resolved in one place"
    assert 'GetEnvironmentVariableA("FARVERPAL_DPS_ALLOW_UNCORROBORATED"' in gate.group(0)
    assert "default" not in gate.group(0).lower(), "no implicit enablement"
    assert "dev override" in gate.group(0)
    assert "on = n > 0 && n < sizeof(val)" in gate.group(0), (
        "the verdict IS the env var's value")
    assert "return on;" in gate.group(0)
    # ... and the arm that used it is logged as a one-route arm.
    assert "alone (dev override" in code
    assert 'hlb_route_name(route)' in code, "the log names the route it armed on"


def test_the_table_base_is_measured_as_a_run_of_code_pointers():
    """The structural locate: the array's LENGTH, a run of code pointers, and a
    base taken from that run's own boundaries - no generated index in sight."""
    code = _verify_code()
    assert "FARVER_DPS_NNATIVES" in code and "FARVER_DPS_TABLE_ENTRIES" in code
    for helper in ("hlb_refresh_exec_ranges", "hlb_ptr_is_runtime_code",
                   "hlb_exec_run_bounds", "hlb_slot_triggers_table"):
        assert len(_definitions(code, helper)) == 1, f"{helper} is defined once"
    # The two bases handed to the predicate are run boundaries, not offsets:
    # the last N slots of the run, and its first - both taken as they are.
    assert re.search(r"run_end\s*-\s*\(size_t\)FARVER_DPS_TABLE_ENTRIES", code)
    assert "bases[nbases++] = (uintptr_t *)run_start;" in code
    assert not re.search(r"run_start\s*[-+]", code), "the run start is nudged"


def test_a_slot_owned_by_another_detour_is_refused_not_reported_armed():
    """MinHook keeps ONE detour per target and answers MH_ERROR_ALREADY_CREATED
    to every later attempt, with no way to ask who owns the slot. The arming
    helper used to call that answer success - so a second detour aimed at an
    already-hooked address logged `-> OK`, was counted as armed, and never ran:
    the "armed but silent" symptom arriving as a lie in the arm report, which
    sends the next session hunting the game instead of the overlap.

    Nothing outside can check this (no test forks the game process), so the
    properties are pinned where they live.
    """
    core = _text(_CORE)
    start = core.index("static bool bridge_try_arm_hook_on(")
    body = core[start:core.index("\n}", start)]
    create = body.index("MH_CreateHook(")

    # Ownership is consulted BEFORE anything is created, and a claim held by a
    # different detour ends the attempt right there - MinHook patches only in
    # MH_EnableHook, so refusing early costs the game nothing and the detour
    # that does own the slot keeps it.
    assert body.index("bridge_claim_owner(") < create
    before = body[body.index("claim->detour != detour"):create]
    assert "bridge_refuse_arm(" in before and "return false;" in before
    assert "already hooked by a different detour" in before
    # A registry that cannot say the slot is free must refuse too: "assumed
    # free" is the behaviour being removed, in either direction.
    assert "cannot be proven" in body[:create]

    # The claim is recorded at CREATE, not at enable: a create whose enable
    # failed transiently leaves the target untouched and comes back through the
    # retry loop as ALREADY_CREATED - which is only recognisable as ours
    # because the claim was taken here.
    after = body[create:]
    assert after.index("bridge_claim_add(") < after.index("MH_EnableHook(")
    # ... and an ALREADY_CREATED slot with no claim is somebody else's hook: no
    # captured original exists, so this detour would never run.
    assert "MH_ERROR_ALREADY_CREATED && !claim" in after
    assert "not this bridge" in after

    # A refusal is never reported as armed, and never waited out as a timeout.
    refused = core[core.index("static void bridge_refuse_arm("):]
    refused = refused[:refused.index("\n}")]
    assert 'send_status("hook", buf, 0)' in refused, "a refusal must fail loudly"
    # Every flag the loop waits on must be settled-aware, or that one hook keeps
    # the loop spinning its full 150 x 200 ms and the failure it finally reports
    # is a timeout wearing the same words as the refusal.
    for loop, flags in (
        ("bridge_arm_all_hooks",
         ("h_inflict", "h_fx", "h_hp", "h_kill", "h_die_base")),
        ("bridge_arm_tables",
         ("h_fx", "h_hp", "h_inflict[t]", "h_kill[t]", "h_die_base[t]")),
    ):
        block = core[core.index(f"static bool {loop}("):]
        block = block[:block.index("\n}")]
        for flag in flags:
            assert f"bridge_arm_settled({flag}," in block, (
                f"{loop} waits on a refused {flag} until the loop expires")


def test_the_damage_capture_counts_every_hit_it_sends():
    """A working capture was once recorded as dead by grepping the wrong form.

    In the 2026-09-30 session the armed `ent.Unit.onInflictDamage` hook fired
    (190 outgoing, then 12 and 25 incoming on the player), but its diagnostic
    line was named after the method - `[diag] onInflictDamage #1 dealer=...` -
    while the *watch probe's* lines were named `[diag] WATCH <type>.<method>`.
    A reader grepping the watch form found nothing, and the damage hook was
    written down as silent: a false statement about the game, which sends the
    next session hunting a pipeline that already works.

    The permanent counter is what makes that misreading impossible, so the
    properties that make it honest are pinned here: it is incremented in the
    ONE hit-payload builder (both directions), so it counts hits exactly and
    never a heal or a kill; the first-hit line is one-shot and names the
    HOOKED method rather than a probe; and every heartbeat carries the total.
    """
    core = _live_core()

    start = core.index("static void send_hit_event(")
    builder = core[start:core.index("\n}\n", start)]
    assert builder.count("note_hit_event(") == 1, "the one builder counts the hit"
    # ...and after the send, so a count is never taken for a payload that a
    # later failure stopped from leaving the builder.
    assert builder.index("send_event(json_buf)") < builder.index("note_hit_event(")

    start = core.index("static void note_hit_event(")
    note = core[start:core.index("\n}\n", start)]
    assert "InterlockedIncrement(&g_hits_sent)" in note
    assert "n != 1" in note, "the first-hit line must be one-shot"
    assert "[hits] first damage event sent" in note
    # The line has to name the CAPTURE. A message built from a FINDEX_WATCH_*
    # name would re-create the exact confusion it exists to end.
    assert "FINDEX_INFLICT_DAMAGE_NAME" in note
    assert "FINDEX_WATCH" not in note

    start = core.index("static void bridge_heartbeat_loop(")
    heartbeat = core[start:core.index("\n}\n", start)]
    assert '\\"hits\\":%ld' in heartbeat, "the heartbeat carries the running total"
    assert "g_hits_sent" in heartbeat


def test_every_generated_findex_is_distinct_and_generator_owned():
    """The manifest is the one place a positional number may live, and no two
    generated macros may name one slot.

    Two contracts, checked on the artifact itself:
      * every numeric macro in findex_manifest.h is declared by
        gen_findex_manifest.py - a hand-written number in a generated file is
        the staleness the generator exists to end, and it would ship silently;
      * no two of them share a value. MinHook keeps ONE detour per target, so
        two names for one findex is a hook (or a probe) that can never install:
        the "armed but silent" overlap, read from a log.

    And a third, in the other direction: the header carries NOTHING the bridge
    does not detour. The diagnostic watch candidates were generated into it for
    a while, and no build armed a single one - the header is what the DLL
    compiles, so a "candidate" there is a function the shipping binary names
    and never hooks. The probe scripts that would visit them again keep their
    own candidate list (`hook_probe/`), which is where the 2026-09-30 session's
    answers belong.
    """
    header = _text(_HOOKS / "farever_dps" / "findex_manifest.h")
    numbers = dict(re.findall(
        r"(?m)^#\s*define\s+((?:FINDEX|ANCHOR)_[A-Z0-9_]+)\s+(\d+)\s*$", header))
    assert numbers, "the generated manifest carries the findexes"
    generator = _text(_HOOKS.parent / "gen_findex_manifest.py")
    hand_written = sorted(n for n in numbers if n not in generator)
    assert not hand_written, (
        f"numeric macro(s) in the generated header that the generator does not "
        f"declare: {hand_written}")
    by_value = {}
    for name, value in sorted(numbers.items()):
        by_value.setdefault(value, []).append(name)
    shared = {value: names for value, names in by_value.items() if len(names) > 1}
    assert not shared, f"two generated macros name one findex: {shared}"
    watch = sorted(n for n in numbers if n.startswith("FINDEX_WATCH_"))
    assert not watch, (
        f"the shipping header describes function(s) no build arms: {watch} - "
        f"probing is a diagnostic, and its candidates belong in the probe "
        f"scripts under hook_probe/, not in the header the DLL compiles")


def test_the_findex_audit_knows_every_macro_the_manifest_defines():
    """hook_probe/findex_audit.py re-derives every generated findex from the
    live hlboot.dat and compares it, from its OWN copy of the names and macros
    - an audit that imports the generator's lists can only ever agree with
    them, so a generator bug would be confirmed instead of caught.

    That independence is exactly what can drift: a target added to the
    generator and not to the audit is a findex the audit silently stops
    covering. The two spellings are pinned together here, at the source level,
    so the drift is a test failure rather than a quiet gap.
    """
    header = _text(_HOOKS / "farever_dps" / "findex_manifest.h")
    generated = set(re.findall(
        r"(?m)^#\s*define\s+((?:FINDEX|ANCHOR)_[A-Z0-9_]+)\s+\d+\s*$", header))
    assert generated, "the generated manifest carries the findexes"
    audit = _text(_HOOKS.parent / "hook_probe" / "findex_audit.py")
    start = audit.index("ARMED = (")
    # The three tuples run to the end of the ANCHORS block; a bare ")" would
    # stop at the first entry's inner tuple and silently halve the comparison.
    stop = audit.index("\n)\n", audit.index("ANCHORS = ("))
    # Comments are not declarations. The block carries prose - including the
    # note about the watch candidates that used to live beside these lists - and
    # a macro spelled in a comment is not one the audit checks.
    block = "\n".join(line.split("#", 1)[0] for line in audit[start:stop].splitlines())
    declared = set(re.findall(r"\b((?:FINDEX|ANCHOR)_[A-Z0-9_]+)\b", block))
    assert declared, "the audit declares the macros it checks"
    unknown = sorted(generated - declared)
    assert not unknown, (
        f"the manifest defines macro(s) the audit does not check: {unknown} - "
        f"add them to hook_probe/findex_audit.py")
    extra = sorted(declared - generated)
    assert not extra, (
        f"the audit checks macro(s) the manifest does not define: {extra} - "
        f"regenerate the manifest, or drop them from the audit")


# --- the build gate: a manifest from another patch must not be built ---------
_GENERATOR = _HOOKS.parent / "gen_findex_manifest.py"
_BUILD = _HOOKS.parent / "build_all.bat"


def _generator():
    """The manifest generator, imported from the hooks tree.

    Cheap on purpose: it reaches crashlink only inside `_load_bytecode`, so the
    digest gate can be exercised here without the game, a bytecode parser, or
    the HL6 shim - and a gate that needed all three would be a gate that could
    itself fail to run.
    """
    spec = importlib.util.spec_from_file_location("_gen_findex_manifest", _GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest(path, digest):
    path.write_text(
        f'/* AUTO-GENERATED (test) */\n#define FARVER_DPS_HLBOOT_SHA256 "{digest}"\n',
        encoding="utf-8")
    return path


def test_the_build_gate_refuses_a_manifest_from_another_hlboot(tmp_path):
    """A header generated from a different hlboot.dat must not be buildable.

    Every findex in the manifest is positional, so a header that describes the
    wrong patch names the wrong functions - the bridge arms them with full OKs
    and captures nothing. The DLL already re-hashes at load and refuses to arm
    on a mismatch; this is that refusal moved into the build, so the binary is
    never produced at all.
    """
    gate = _generator().gate_committed_manifest
    game = tmp_path / "hlboot.dat"
    game.write_bytes(b"the installed build")

    old = hashlib.sha256(b"a previous build").hexdigest()
    problems = gate(_manifest(tmp_path / "stale.h", old), game)
    assert problems, "a manifest from another patch was accepted"
    text = "\n".join(problems)
    assert old[:12] in text and hashlib.sha256(b"the installed build").hexdigest()[:12] in text, (
        "the finding must name BOTH digests - 'it differs' is not actionable")
    assert "gen_findex_manifest.py" in text, "the finding must say how to fix it"
    assert "other functions" in text, "the finding must say what it would cost"


def test_the_build_gate_accepts_a_manifest_from_the_installed_game(tmp_path):
    """The happy path, and the reason the gate is a digest and not a re-resolve:
    a manifest whose digest matches is the one the generator just wrote."""
    gate = _generator().gate_committed_manifest
    game = tmp_path / "hlboot.dat"
    game.write_bytes(b"the installed build")
    digest = hashlib.sha256(b"the installed build").hexdigest()
    assert gate(_manifest(tmp_path / "fresh.h", digest), game) == []


def test_the_build_gate_refuses_what_it_cannot_judge(tmp_path):
    """No header, no digest, no installed game: each is a different kind of
    'cannot tell', and none of them may pass as a clean build. The missing-game
    case is the one that decides the archive question - the generator falls
    back to a copy of hlboot.dat when the game is not in the Steam path, and a
    gate that fell back too would be the very mistake it exists to catch."""
    gate = _generator().gate_committed_manifest
    game = tmp_path / "hlboot.dat"
    game.write_bytes(b"the installed build")
    digest = hashlib.sha256(b"the installed build").hexdigest()

    assert gate(tmp_path / "absent.h", game), "a manifest that does not exist"
    old = tmp_path / "predates_digests.h"
    old.write_text("#define FINDEX_INFLICT_DAMAGE 4932\n", encoding="utf-8")
    assert gate(old, game), "a hand-written header with no recorded digest"
    assert gate(_manifest(tmp_path / "ok.h", digest), tmp_path / "no_game.dat"), (
        "a missing game was treated as agreement")


def test_the_gate_believes_the_installed_game_and_nothing_else():
    """Generation may fall back to an archived hlboot.dat; the gate may not.

    An archive resolves every findex perfectly, so a manifest built from one is
    internally valid and describes a build nobody is playing - the case the
    per-value audits cannot see, because they check the header against whatever
    bytecode they were pointed at.
    """
    gen = _generator()
    # By VALUE, not identity: the search order now comes from hlboot_reader (one
    # list, shared with both audits, so the three cannot drift), which builds
    # each Path separately. What matters is that generation prefers the installed
    # game over any archived copy.
    assert gen.HLBOOT_CANDIDATES[0] == gen.INSTALLED_HLBOOT, (
        "the installed game must be the first thing generation looks at")
    source = _text(_GENERATOR)
    gate_branch = source[source.index("if args.gate:"):source.index("hlboot = args.hlboot")]
    assert "INSTALLED_HLBOOT" in gate_branch
    assert "HLBOOT_CANDIDATES" not in gate_branch, (
        "the gate must not resolve a fallback copy: that is the case it exists for")
    assert "collect(" not in gate_branch, (
        "the gate must stay digest-only - crashlink can fail for reasons of its own")


def test_the_build_gates_the_manifest_before_it_compiles():
    """The gate has to sit between the generator and the first gcc.

    After it, the build compiles the header it just proved correct. Before the
    generate step, it would block the ordinary correct case (a patched game
    whose committed header is legitimately stale until regenerated). And a gate
    that only warned would let a binary that refuses itself at load get
    published - which is the state this whole mechanism exists to prevent.
    """
    build = _text(_BUILD)
    # The generate step and the gate step invoke the SAME script, so looking for
    # the filename finds the gate's own line when the two are ever swapped - the
    # index would then equal itself and "generate < gate" would pass on a
    # misordered build. The generate call ends its line right after the closing
    # quote; the gate's is followed by " --gate". Require the newline.
    generate = re.search(r'gen_findex_manifest\.py"\r?\n', build).start()
    gate = build.index("--gate")
    compile_ = build.index("gcc -O2")
    assert generate < gate < compile_, (
        "the gate must run after generation and before the first compile")

    between = build[gate:compile_]
    # The gate's own block, not the whole span: the steps after it have their
    # own exits, and what matters is that THIS one stops the build.
    gate_block = build[gate:build.index("if !ERRORLEVEL! equ 2 (", gate)]
    assert "if !ERRORLEVEL! equ 1 (" in gate_block, "a failed gate is not inspected"
    assert gate_block.count("exit /b 1") == 1, (
        "a failed gate must stop the build with exit 1")
    assert "if !ERRORLEVEL! equ 2 (" in between, (
        "'no game installed' must warn and continue, not fail the build")
    # The gate is about the INSTALLED game, and the message has to say so: a
    # developer who sees a red build needs to know which file disagreed.
    assert "--gate" in between and "installed game" in between


def test_the_build_proves_the_manifest_against_the_live_bytecode():
    """The last step judges what was just PUBLISHED, by three independent readers.

    Regeneration can only ever be self-consistent: the generator writes the
    header, so the header agrees with the generator. Something has to check it
    against the bytecode the game actually runs, and nothing did until this
    step. The placement is the point - after the publish check, because the
    binaries are what the verdict is about, and before the build's exit, so a
    finding still fails the run.

    Three readers, not one: `--check` re-resolves with the same code that wrote
    the header (catching a rename, or a patch landing between the two), while
    the two audits carry their own hardcoded lists, so a generator bug shows up
    as a disagreement instead of being confirmed by its own logic. The
    generator's flag is the only `--check`; the audits take none.
    """
    build = _text(_BUILD)
    step = build.index("[6/6] Proving the manifest")
    assert build.index("Verified %%F matches the built file") < step, (
        "the proof must judge the published binaries, so it comes after the "
        "publish check")
    assert build.index("gcc -O2") < step, "nothing to prove before anything is built"

    verdict = build.index("if defined BUILD_FAILED (")
    assert step < verdict, "a finding must be able to fail the run"

    block = build[step:verdict]
    for tool in ("gen_findex_manifest.py", "hook_probe\\findex_audit.py",
                 "hook_probe\\anchor_audit.py"):
        assert tool in block, f"the build no longer names {tool}"
    # ONE invocation now: hook_probe\verify_all.py runs all three readers over a
    # single shared parse (see test_the_manifest_proof_parses_the_bytecode_once).
    # Counted on the lines that actually execute something - the /noverify
    # warning prints the same three commands by hand, prefixed with `echo`, and
    # the REM block explains the step in prose. Three invocations here means the
    # three parses are back, which is the ~22s this step used to cost.
    code = "\n".join(line for line in block.splitlines()
                     if not line.strip().upper().startswith("REM"))
    invocations = [ln.strip() for ln in code.splitlines()
                   if ln.strip().startswith('"%PYTHON_EXE%"')]
    assert len(invocations) == 1, (
        f"the proof must be one process, got {len(invocations)}: {invocations}")
    assert "hook_probe\\verify_all.py" in invocations[0], (
        f"the proof step runs something else: {invocations[0]}")
    # --check belongs to the generator alone, and only when it is run AS A
    # SCRIPT. The driver calls its check entry point directly, over the parse it
    # already has; the flag survives only in the manual instructions.
    assert "--check" not in invocations[0], (
        "the driver runs all three readers in one process; --check would mean "
        "the generator was shelling out again")

    failed = block[block.index("if defined VERIFY_FAILED ("):
                   block.index("if defined VERIFY_INCONCLUSIVE (")]
    warned = block[block.index("if defined VERIFY_INCONCLUSIVE ("):]
    assert 'set "BUILD_FAILED=1"' in failed, (
        "a finding must fail the build - the numbers in the published binaries "
        "are then suspect")
    # Counted across the WHOLE step, not just the summary: the exit-2 branch
    # lives inside the tool loop, and a version of it that set BUILD_FAILED
    # would fail the build for a machine with no game installed - which is how
    # this step gets switched off. One place may set it, and it is the finding.
    assert block.count('set "BUILD_FAILED=1"') == 1, (
        "only a FINDING may fail the build; exit 2 (could not run) must not")
    assert 'set "VERIFY_INCONCLUSIVE=1"' in block, (
        "an unrunnable check has to be recorded, not dropped")
    assert "UNVERIFIED" in warned, (
        "an unrunnable check must say it proved nothing, not read as a pass")


#: The app-side wrapper, which is where a build is actually launched from.
_WRAPPER = _ROOT / "dps_bridge" / "build_all.bat"


def test_the_manifest_proof_is_skipped_only_by_asking_for_it():
    """`/noverify` exists so a C-only iteration does not pay 34s - and it must
    stay the only way to skip the proof.

    A shortcut becomes the default by accident: someone adds the flag to the
    script the launcher calls, or inverts the condition once to "test the fast
    path" and forgets. Both are caught here by pinning the ONE line that sets
    SKIP_VERIFY, and that it sits in the argument parser.
    """
    build = _text(_BUILD)
    sets = [m.start() for m in re.finditer(r'set "SKIP_VERIFY=1"', build)]
    assert len(sets) == 1, (
        f"SKIP_VERIFY is set {len(sets)} times; exactly one may - the argument "
        f"parser - or the proof can be turned off without asking")
    gate = build.index("Gating the manifest against the installed game")
    assert sets[0] < gate, "the flag is parsed at the top, before anything is built"


def test_skipping_the_proof_cannot_reach_the_gate():
    """The cheap check stays on. That is the whole reason the flag is safe.

    The gate is a digest comparison with no crashlink, and it is the one that
    stops a build whose manifest is not about the installed game - wrong
    numbers on the wrong functions. `/noverify` may only cost the redundant
    name-level re-derivation, so nothing between the gate and the first compile
    may mention SKIP_VERIFY.
    """
    build = _text(_BUILD)
    gate = build.index("Gating the manifest against the installed game")
    before_first_compile = build[gate:build.index("gcc -O2")]
    assert "SKIP_VERIFY" not in before_first_compile, (
        "/noverify must not be able to reach the gate - that one is not a "
        "convenience, it is the difference between right numbers and wrong ones")


def test_a_skipped_proof_says_so_loudly_and_twice():
    """Unmissable, at the step AND as the last line of the build.

    The step is where a reader is deciding whether to trust the binaries; the
    final line is what is still on screen when the caller's banner appears over
    the top of it (a delegated build prints no banner of its own). And the
    warning has to carry the commands, or "unverified" is just a worry.
    """
    build = _text(_BUILD)
    step = build.index("[6/6] Proving the manifest")
    verdict = build.index("if defined BUILD_FAILED (")
    block = build[step:verdict]

    warning = block[block.index("if defined SKIP_VERIFY ("):
                    block.index("for %%T in (")]
    assert "UNVERIFIED" in warning, "the skip must name what it did not do"
    assert "findex_audit.py" in warning and "--check" in warning, (
        "the warning must print how to prove the manifest by hand")
    assert "digest" in warning, (
        "say what WAS still checked, or the warning oversells the risk")

    # The closing line sits between the summaries and the build's verdict, so on
    # a successful delegated run it is the last thing the native builder prints
    # - the caller's banner is all that follows it.
    closing = block.index("if defined SKIP_VERIFY echo")
    final = block[closing:]
    assert "UNVERIFIED" in final and "/noverify" in final, (
        "the last line of a skipped build must say it was skipped")
    assert closing > block.index("if defined VERIFY_INCONCLUSIVE ("), (
        "it must come after the loop and both summaries, or it is not the "
        "last word")

    # ...and the per-tool lines still appear, so a scrolled log shows three.
    assert "SKIPPED ^(/noverify^)" in block, (
        "each skipped tool prints its own line rather than the step going quiet")


def test_an_unknown_argument_is_refused_instead_of_ignored():
    """A typo must not look like a flag that did nothing.

    `/noverifiy` used to be the worst case: silently accepted, proof run as
    usual, and a developer who believed they had the fast build got the slow
    one. Worse in the other direction is a typo that quietly disabled the proof.
    """
    build = _text(_BUILD)
    # Both halves are required, and naming only one of them is the failure that
    # would pass a loose check: a consumer renamed to something never matched
    # still leaves `BAD_ARG` in the parser, and the build then accepts typos
    # again while the text still looks like it refuses them.
    parser = build[build.index("for %%A in (%*) do ("):]
    assert 'else (set "BAD_ARG=' in parser, (
        "the argument parser must set BAD_ARG for anything it does not know")
    assert "if defined BAD_ARG (" in build, "and something must act on it"
    assert "exit /b 2" in build, "an unknown argument must stop the build"
    bad = build.index("if defined BAD_ARG (")
    assert bad < build.index("Generating FareverMod semantic version header"), (
        "refused before anything is generated, let alone compiled")


def test_the_wrapper_forwards_the_flag_to_the_native_builder():
    """Otherwise the documented flag does not exist for the way builds start.

    The app's build_all.bat is what a developer launches, and it called the
    native builder with a hardcoded `/nopause`: every other argument was
    dropped on the floor. Both paths have to forward - the PowerShell tee and
    the plain fallback, or the flag works only when PowerShell is missing.
    """
    if not _WRAPPER.is_file():
        pytest.skip("app-side build wrapper is not present in this checkout")
    wrapper = _text(_WRAPPER)
    calls = [line for line in wrapper.splitlines() if "HOOKS_BUILD%" in line
             and ("call " in line or "& " in line)]
    assert calls, "the wrapper no longer invokes the native builder"
    for line in calls:
        assert "%*" in line, (
            f"this call drops the launcher's arguments: {line.strip()}")
    assert len(calls) == 2, (
        "expected both the PowerShell tee and the plain fallback to forward; "
        f"found {len(calls)}")


# --- one parse, three readers: hook_probe/verify_all.py -----------------------
# The proof ran three processes, and each parsed the 14MB hlboot.dat for itself:
# ~11s each, ~34s for the build's own last word on the numbers it just published.
# Parsing is mechanical - read the file, index the strings, walk the types - so
# hlboot_reader.load() now does it once and verify_all.py hands the parsed
# bytecode to all three readers. ~12s instead of ~34s.
#
# What must NOT come with the sharing: a reader's own list of names, its
# comparisons, or its ability to disagree. That is what the next tests pin.
_VERIFY_ALL = _HOOKS.parent / "hook_probe" / "verify_all.py"


def _verify_all():
    """hook_probe/verify_all.py, imported.

    Nothing here touches crashlink at import time - the parse lives inside
    hlboot_reader.load() - so the driver can be exercised with a fake bytecode
    on a machine that has neither the game nor a bytecode parser.
    """
    spec = importlib.util.spec_from_file_location("_verify_all", _VERIFY_ALL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_verify_all(tmp_path, monkeypatch, *, gen=0, findex=0, anchor=0,
                    raise_in=None, unparseable=False):
    """verify_all.main() with the parse faked and the readers' exits dictated.

    Returns (exit code, calls), where `calls` records every entry point the
    driver actually reached - so a test can see both that all three ran and that
    the bytecode was parsed once for all of them.
    """
    verify = _verify_all()
    tmp_path.mkdir(parents=True, exist_ok=True)
    manifest = tmp_path / "findex_manifest.h"
    manifest.write_text(
        '#define FARVER_DPS_HLBOOT_SHA256 "%s"\n' % ("0" * 64), encoding="utf-8")
    game = tmp_path / "hlboot.dat"
    game.write_bytes(b"a file that is not really bytecode")

    calls = []

    def fake_load(path):
        calls.append("parse")
        if unparseable:
            raise RuntimeError("bytecode version 6 is newer than crashlink knows")
        return object()

    def reader(name, rc):
        def run(*args, **kwargs):
            # The parsed bytecode has to actually ARRIVE: a reader handed None
            # would go and parse the 14MB file itself, which is the one thing
            # this driver exists to stop. Recorded under a different name so
            # the expectation below fails loudly rather than quietly.
            bc = kwargs.get("bc", args[0] if args else None)
            calls.append(name if bc is not None else f"{name}(RE-PARSED)")
            if raise_in == name:
                raise RuntimeError("this reader is broken")
            return rc
        return run

    monkeypatch.setattr(verify.hlboot_reader, "find_hlboot",
                        lambda explicit=None: str(game))
    monkeypatch.setattr(verify.hlboot_reader, "sha256_of", lambda path: "0" * 64)
    monkeypatch.setattr(verify.hlboot_reader, "load", fake_load)
    monkeypatch.setattr(verify.gen_findex_manifest, "check_manifest",
                        reader("gen_findex_manifest", gen))
    monkeypatch.setattr(verify.findex_audit, "audit", reader("findex_audit", findex))
    monkeypatch.setattr(verify.anchor_audit, "audit", reader("anchor_audit", anchor))
    monkeypatch.setattr(sys, "argv", ["verify_all.py", "--manifest", str(manifest)])

    before = list(sys.path)
    try:
        rc = verify.main()
    finally:
        # The driver puts its own directory on sys.path to find its two sibling
        # scripts; a test suite must not inherit that.
        sys.path[:] = before
    return rc, calls


def test_the_manifest_proof_parses_the_bytecode_once(tmp_path, monkeypatch):
    """The whole point: three readers, ONE parse.

    ~11s per parse is the cost being removed, and it is invisible to every
    other check - all three readers still run, still compare, still return their
    own verdict. What this pins is that a fourth reader, or a "small" helper
    that resolves one name of its own, cannot quietly reintroduce the cost.
    """
    rc, calls = _run_verify_all(tmp_path, monkeypatch)
    assert rc == 0, f"all three readers agreeing must exit 0, got {rc}"
    assert calls == ["parse", "gen_findex_manifest", "findex_audit", "anchor_audit"], (
        f"expected one parse and then all three readers, got {calls}")


def test_the_proof_is_only_quiet_when_all_three_agree(tmp_path, monkeypatch):
    """Exit codes are what the build reads, so the whole truth table is pinned.

    0 all three agree. 1 a finding - and a finding outranks an unrunnable
    reader, because "one is wrong" is worse news than "one could not run".
    2 could not run at all: no hlboot.dat, or bytecode the parser refuses. That
    has to stay a WARNING, because a machine without the game installed cannot
    prove anything and the gate has already refused to compile for it.
    """
    rc, _ = _run_verify_all(tmp_path / "a", monkeypatch, gen=1)
    assert rc == 1, "a stale header is a finding"
    rc, _ = _run_verify_all(tmp_path / "b", monkeypatch, findex=1)
    assert rc == 1, "the independent audit's finding is a finding"
    rc, _ = _run_verify_all(tmp_path / "c", monkeypatch, anchor=1)
    assert rc == 1, "a wrong anchor set is a finding"
    rc, _ = _run_verify_all(tmp_path / "d", monkeypatch, anchor=2)
    assert rc == 2, "a reader that could not run is inconclusive, not a failure"
    rc, _ = _run_verify_all(tmp_path / "e", monkeypatch, gen=1, anchor=2)
    assert rc == 1, "a finding outranks an unrunnable reader"
    rc, calls = _run_verify_all(tmp_path / "f", monkeypatch, unparseable=True)
    assert rc == 2, "unparseable bytecode is inconclusive"
    assert calls == ["parse"], (
        "with no bytecode there is nothing to check, so no reader may run")


def test_a_reader_that_crashes_is_a_finding_not_a_pass(tmp_path, monkeypatch):
    """The hole the single process closed.

    As three separate processes, a reader that raised an exception died with
    some exit code that was neither 0, 1 nor 2 - and the build's three-way test
    matched none of them, so it passed SILENTLY. A broken audit proving nothing
    read exactly like an audit that agreed. Here it is exit 1, and the other two
    still run: one broken reader must not hide the other two's verdicts.
    """
    rc, calls = _run_verify_all(tmp_path, monkeypatch, raise_in="findex_audit")
    assert rc == 1, "a crashed reader must fail the build, not pass it"
    assert calls == ["parse", "gen_findex_manifest", "findex_audit", "anchor_audit"], (
        f"the other two readers must still run after one raises: {calls}")


def _prose_free(text):
    """`text` with its module docstring and `#` comments removed.

    Prose is not an assertion. Every one of these files spells out in its
    documentation exactly which macros it is talking about - `ANCHOR_*` above all
    - and reading that as the driver resolving one would fail on the
    documentation of the very thing being checked. A `#` inside a string would
    also be lost; there are none after the docstring.
    """
    end = text.index('"""', text.index('"""') + 3) + 3
    return "\n".join(line.split("#", 1)[0] for line in text[end:].splitlines())


def test_the_shared_parse_cost_nothing_that_checks(tmp_path, monkeypatch):
    """Sharing the PARSE must not start sharing the ANSWERS.

    The whole reason there are three readers is that they can disagree: each
    spells out its own list of names, so a generator bug shows up as a
    disagreement instead of being confirmed by its own logic. A driver that
    resolved a findex and handed it over would make all three agree with the
    generator by construction - the failure this arrangement was built to
    prevent, arrived at through tidying.
    """
    source = _text(_VERIFY_ALL)
    for macro in ("FINDEX_", "ANCHOR_"):
        assert macro not in _prose_free(source), (
            f"verify_all.py must not name {macro}* itself: it runs readers, it "
            "does not resolve anything. A macro here means one reader's answer "
            "is being handed to another.")
    # What each reader is handed: the parsed bytecode and the header on disk.
    # Nothing pre-resolved, so each one still does its own lookups.
    assert "gen_findex_manifest.check_manifest(manifest, Path(hlboot), bc=bc)" in source
    assert "findex_audit.audit(bc, str(manifest), hlboot" in source
    assert "anchor_audit.audit(bc, str(manifest), hlboot)" in source
    for name in ("findex_audit", "anchor_audit"):
        audit = _text(_HOOKS.parent / "hook_probe" / f"{name}.py")
        assert "import gen_findex_manifest" not in audit, (
            f"{name}.py imports the generator: an audit that can only agree with "
            "the thing it audits is not a check")


def test_each_reader_still_runs_on_its_own(tmp_path):
    """Sharing must not make a tool unreachable.

    verify_all.py is how the BUILD proves the manifest; it is not the only way to
    ask. A developer chasing a failure opens one reader and runs it by hand, and
    that reader resolves its own bytecode - so each keeps a main() that parses
    for itself.
    """
    for name in ("findex_audit", "anchor_audit"):
        source = _text(_HOOKS.parent / "hook_probe" / f"{name}.py")
        assert "def main(" in source, f"{name}.py is no longer runnable by hand"
        assert source.count("hlboot_reader.load(") == 1, (
            f"{name}.py should parse exactly once, for its own standalone run")
        # ...and never inside the shared-parse surface it hands to the driver.
        body = source[source.index("def main("):]
        assert "hlboot_reader.load(" in body, (
            f"{name}.py must resolve its own bytecode when run by hand")
