"""Headless tests for the DPS capture subsystem -- one section per module:

* coordinator (core/dps_source.py): DamageSourceManager owns the shared event
  ring, the combined status the UI reads, and the routing/isolation rules that
  keep the two engines from being edited into each other.
* bridge (core/dps_bridge.py): Options 1 (proxy) & 2 (injector) are ONE capture
  -- the DLL payload contract, hook-install telemetry and the live/idle status.
* memory (core/dps_memory.py): Option 3, the DLL-free scanner -- calibration
  phases, derive cadences, and the park/yield arbitration against the bridge.

The two engines deliberately share no code (asserted in the coordinator
section), so their tests stay in their own sections. The Combat & DPS Analysis
page and the Top DPS overlay are covered by tests/test_dps.py.
"""
from __future__ import annotations

import ast
import re
import threading
import time
from pathlib import Path

import pytest

from farever_companion.constants import HL_FINDEX_INFLICT_DAMAGE
from farever_companion.core import dps_bridge, dps_memory
from farever_companion.core.damage_events import DamageEvent
from farever_companion.core.dps_bridge import (
    HEALTH_LINE_PERIOD_S, PROXY_MODULES, BridgeSource, parse_hook_event,
)
from farever_companion.core.dps_memory import MemorySource
from farever_companion.core.dps_source import DamageSourceManager
from tests import source_guard


# ======================================================================
# coordinator (core/dps_source.py)
# ======================================================================

class _NoScan:
    has_scan = False


class _Scan:
    has_scan = True


def _ev(amount: float, skill: str = "Sword_Base_Attack") -> DamageEvent:
    return DamageEvent(amount=amount, skill=skill)


@pytest.fixture
def make(monkeypatch):
    """A manager whose engines are stubbed out (no threads, no sockets)."""
    def _make(proc=None, **kw):
        mgr = DamageSourceManager(proc or _Scan(), **kw)
        # Stub the real threads/sockets but keep the started flags honest so the
        # status routing under test sees a running engine.
        monkeypatch.setattr(mgr.bridge, "start",
                            lambda: setattr(mgr.bridge, "_started", True))
        monkeypatch.setattr(mgr.memory, "start",
                            lambda: setattr(mgr.memory, "_started", True))
        return mgr
    return _make


# --- event ring -----------------------------------------------------------
def test_ring_cursor_semantics():
    mgr = DamageSourceManager(_Scan())   # threads not started in tests
    c0, evs = mgr.events_after(0)
    assert c0 == 0 and evs == []

    mgr._push([_ev(10.0), _ev(20.0)])
    c1, evs1 = mgr.events_after(c0)
    assert c1 == 2 and [e.amount for e in evs1] == [10.0, 20.0]

    # repeated drain returns nothing new
    c2, evs2 = mgr.events_after(c1)
    assert c2 == 2 and evs2 == []

    # second consumer joins late and only sees what it hasn't consumed
    c3, evs3 = mgr.events_after(1)
    assert c3 == 2 and [e.amount for e in evs3] == [20.0]

    mgr._push([_ev(30.0)])
    c4, evs4 = mgr.events_after(c3)
    assert c4 == 3 and [e.amount for e in evs4] == [30.0]
    mgr.shutdown()


def test_both_engines_feed_one_ring():
    """Whichever option is capturing, consumers drain the same ring."""
    mgr = DamageSourceManager(_Scan())
    # a heal exactly as the bridge parser produces it
    mgr._push([dps_bridge.parse_hook_event(
        '{"t":"heal","amt":450.0,"landed":400.0,"sk":"Regen","src":"You"}')])
    mgr._push([_ev(50.0, "Axe_Base_Attack")])
    _, evs = mgr.events_after(0)
    assert [e.amount for e in evs] == [400.0, 50.0]
    assert evs[0].is_heal is True
    mgr.shutdown()


def test_first_event_stage_timing_recorded_on_any_engine(make):
    mgr = make()
    mgr.ensure_started()
    assert mgr.has_decoded is False
    mgr._push([_ev(1.0)])
    assert mgr.has_decoded is True
    assert "1st event" in mgr.timing_text()
    mgr.shutdown()


# --- status routing -------------------------------------------------------
def test_off_until_started():
    mgr = DamageSourceManager(_Scan())
    assert mgr.status() == "off"        # thread not running
    mgr.shutdown()


def test_status_follows_the_selected_engine(make):
    """Option 1/2 status comes from the bridge, Option 3's from the scanner."""
    mgr = make()
    mgr.ensure_started()

    mgr.set_mode("proxy")
    assert mgr.status() == "missing_proxy"
    mgr.set_mode("injector")
    assert mgr.status() == "need_inject"

    mgr.set_mode("memory")
    assert mgr.status() == "locating"     # scanner owns the status now
    mgr.shutdown()


def test_noscan_is_an_option_3_condition(make):
    """A scanner-less build still works on Options 1 & 2 - only 3 is blocked."""
    mgr = make(proc=_NoScan())
    mgr.ensure_started()

    mgr.set_mode("proxy")
    assert mgr.status() == "missing_proxy"
    mgr.set_mode("memory")
    assert mgr.status() == "noscan"
    mgr.shutdown()


def test_bridge_traffic_ignored_in_option_3(make):
    """Option 3 never yields to the DLL: a leftover mapped bridge must not
    hijack the status, the source name, or the counts while memory owns the
    capture (the bridge engine is stopped on mode switch)."""
    mgr = make()
    mgr.ensure_started()
    mgr.set_mode("memory")
    mgr.bridge._last_event_ts = time.monotonic()
    mgr.bridge.events_n = 42
    assert mgr.status() == "locating"     # scanner owns the status now
    assert mgr.live_source_name() == "memory reader"
    assert mgr.counts() == (0, 0)         # no bridge numbers leak in
    mgr.shutdown()


def test_live_source_name_switches_with_the_capture(make):
    mgr = make()
    mgr.set_mode("injector")
    assert mgr.live_source_name() == "combat bridge (injector)"
    mgr.set_mode("memory")
    assert mgr.live_source_name() == "memory reader"
    mgr.shutdown()


def test_counts_report_the_live_capture(make):
    mgr = make()
    mgr.ensure_started()
    mgr.bridge.events_n = 0
    assert mgr.counts() == (0, 0)

    mgr.bridge._last_event_ts = time.monotonic()
    mgr.bridge.events_n = 42
    assert mgr.counts() == (42, 0)        # bridge numbers, no memory involvement
    mgr.shutdown()


# --- the two engines stay apart -------------------------------------------
def _imported_modules(path: str) -> set[str]:
    tree = ast.parse(source_guard.read_text(path, encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[-1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[-1])
    return names


def test_the_two_engines_share_no_code():
    """A fix for one option must never be able to reach into the other."""
    assert "dps_memory" not in _imported_modules(dps_bridge.__file__)
    assert "dps_bridge" not in _imported_modules(dps_memory.__file__)


def test_options_1_and_2_share_one_bridge_module():
    """Both DLL options are the same capture, so both come from dps_bridge."""
    assert set(dps_bridge.BRIDGE_MODES) == {"proxy", "injector"}
    assert "memory" not in dps_bridge.BRIDGE_MODES


# --- plumbing to the engines ----------------------------------------------
def test_dmg_hints_env_override_beats_settings(monkeypatch):
    """FAREVER_DMG_* env overrides beat settings-cached pointers."""
    from farever_companion.ui.game_attach import dmg_hints_from

    class _S:
        dmg_display_type = "0x1111"
        dmg_result_type = "0x2222"

    monkeypatch.delenv("FAREVER_DMG_TYPE", raising=False)
    monkeypatch.delenv("FAREVER_DMG_RESULT_TYPE", raising=False)
    assert dmg_hints_from(_S()) == ("0x1111", "0x2222")

    # no settings value and no env -> empty (reader scans normally)
    assert dmg_hints_from(type("_S", (), {"dmg_display_type": "",
                                           "dmg_result_type": ""})()) \
        == ("", "")

    monkeypatch.setenv("FAREVER_DMG_TYPE", "0xaaaa")
    monkeypatch.setenv("FAREVER_DMG_RESULT_TYPE", "0xbbbb")
    assert dmg_hints_from(_S()) == ("0xaaaa", "0xbbbb")


def test_type_hint_plumbing():
    """The cached type pointer seeds the reader to skip the locate scan."""
    mgr = DamageSourceManager(_Scan(), type_hint="0x1234")
    assert mgr._ensure_reader()._type_hint == "0x1234"

    mgr2 = DamageSourceManager(_Scan())
    mgr2.set_type_hint("0x9999")
    assert mgr2._ensure_reader()._type_hint == "0x9999"
    mgr.shutdown(); mgr2.shutdown()


def test_result_type_hint_plumbing():
    mgr = DamageSourceManager(_Scan(), result_type_hint="0x5678")
    assert mgr._ensure_reader()._result_type_hint == "0x5678"
    mgr.shutdown()


def test_log_line_fans_out_to_both_engines():
    """One Activity-Log sink; either engine's diagnostics reach it."""
    mgr = DamageSourceManager(_Scan())
    logs: list[str] = []
    mgr.log_line = logs.append
    assert mgr.bridge.log_line == logs.append
    assert mgr.memory.log_line == logs.append
    mgr.bridge._emit_log("bridge says hi")
    mgr.memory._emit_log("scanner says hi")
    assert "bridge says hi" in logs and "scanner says hi" in logs
    mgr.shutdown()


def test_located_pointers_read_reader_cache():
    mgr = DamageSourceManager(_Scan())
    assert mgr.located_pointers() == (None, None)   # no reader yet

    class _R:
        type_ptr = 0x7ffc1234
        _result_type_ptr = 0x7ffc5678

    mgr._reader = _R()
    assert mgr.located_pointers() == (0x7ffc1234, 0x7ffc5678)
    mgr.shutdown()


def test_damage_stale_follows_reader_diag():
    mgr = DamageSourceManager(_Scan())
    assert mgr.damage_stale is False        # no reader, no diag
    assert mgr.diagnostics() == {}

    mgr._reader = type("_R", (), {"_diag": {"displays": 0, "ok": 0}})()
    assert mgr.damage_stale is False        # nothing found yet - just quiet

    mgr._reader = type("_R", (), {"_diag": {"displays": 12, "ok": 0,
                                            "no_res": 11, "bad_amount": 1}})()
    assert mgr.damage_stale is True         # found displays, decoded nothing
    assert mgr.diagnostics()["displays"] == 12


def test_locate_progress_ticks_during_scan():
    mgr = DamageSourceManager(_Scan())
    assert mgr.locate_progress() == ""          # nothing locating yet

    mgr._locate_running = True
    mgr._locate_started = time.monotonic() - 33.0
    mgr._locate_attempts = 1                    # scan 2 in progress
    prog = mgr.locate_progress()
    assert "2" in prog and "33" in prog         # e.g. "scan 2 · 33s"
    mgr._locate_running = False
    assert mgr.locate_progress() == ""
    mgr.shutdown()


def test_recalibrate_reaches_the_scanner():
    mgr = DamageSourceManager(_Scan())
    calls: list[str] = []
    mgr.memory.recalibrate = lambda: calls.append("map")
    mgr.recalibrate()
    assert calls == ["map"]
    mgr.shutdown()


def test_switching_to_memory_starts_the_scanner(monkeypatch):
    mgr = DamageSourceManager(_Scan())
    started: list[str] = []
    stopped: list[str] = []
    monkeypatch.setattr(mgr.bridge, "start",
                        lambda: (started.append("bridge"),
                                 setattr(mgr.bridge, "_started", True)))
    monkeypatch.setattr(mgr.memory, "start",
                        lambda: (started.append("memory"),
                                 setattr(mgr.memory, "_started", True)))
    monkeypatch.setattr(mgr.bridge, "stop", lambda: stopped.append("bridge"))
    monkeypatch.setattr(mgr.memory, "stop", lambda: stopped.append("memory"))

    mgr.ensure_started()                    # Option 1 default
    assert started == ["bridge"]

    mgr.set_mode("memory")
    assert started == ["bridge", "memory"]
    assert stopped == ["bridge"]            # exactly one engine live
    mgr.shutdown()


def test_switching_back_to_a_bridge_option_restarts_the_bridge(monkeypatch):
    """memory -> proxy stops the scanner and restarts the bridge listener."""
    mgr = DamageSourceManager(_Scan())
    started: list[str] = []
    stopped: list[str] = []
    monkeypatch.setattr(mgr.bridge, "start",
                        lambda: (started.append("bridge"),
                                 setattr(mgr.bridge, "_started", True)))
    monkeypatch.setattr(mgr.memory, "start",
                        lambda: (started.append("memory"),
                                 setattr(mgr.memory, "_started", True)))
    monkeypatch.setattr(mgr.bridge, "stop", lambda: stopped.append("bridge"))
    monkeypatch.setattr(mgr.memory, "stop", lambda: stopped.append("memory"))

    mgr.set_mode("memory")
    mgr.ensure_started()
    assert started == ["memory"]

    mgr.set_mode("proxy")
    assert stopped == ["memory"]
    assert started == ["memory", "bridge"]
    assert mgr.status() == "missing_proxy"  # bridge owns the status again
    mgr.shutdown()


def test_set_mode_before_start_leaves_both_engines_stopped(make):
    """Picking a mode pre-attach must not spin up any thread."""
    mgr = make()
    mgr.set_mode("memory")
    assert mgr.bridge.started is False
    assert mgr.memory.started is False
    assert mgr.status() == "off"
    mgr.shutdown()


def test_bridge_ignores_non_bridge_modes():
    """Options 1 & 2 share one bridge file; 'memory' must not retarget it."""
    from farever_companion.core.dps_bridge import BridgeSource
    b = BridgeSource(proc=_Scan(), emit=lambda evs: None)
    b.set_mode("injector")
    b.set_mode("memory")                    # not a bridge mode: kept
    assert b.mode == "injector"
    b.set_mode("proxy")
    assert b.mode == "proxy"
    b.stop()


# --- compatibility surface ------------------------------------------------
def test_hook_parser_shim_matches_the_bridge_parser():
    mgr = DamageSourceManager(_Scan())
    line = '{"t":"hit","amt":1250.5,"crit":1,"sk":"Thunderclap","src":"Me","me":1}'
    assert mgr._parse_hook_event(line).amount == 1250.5
    assert mgr._parse_hook_event("not json") is None
    mgr.shutdown()


def test_bridge_status_shim_reaches_the_bridge():
    mgr = DamageSourceManager(_Scan())
    mgr._handle_bridge_status({"t": "status", "s": "boot", "ok": 1, "m": "boot"})
    mgr._handle_bridge_status({"t": "status", "s": "table", "ok": 0, "m": "miss"})
    assert "miss" in mgr.bridge_issue()
    mgr.shutdown()


def test_hook_event_counters_delegate_to_the_bridge():
    mgr = DamageSourceManager(_Scan())
    mgr.bridge.events_n = 42
    mgr.bridge._last_event_ts = time.monotonic()
    assert mgr._hook_events_n == 42
    assert mgr._hook_last_ts > 0.0
    mgr.shutdown()


# --- status-line text -----------------------------------------------------
def test_timing_text_stages(make):
    mgr = make()
    assert mgr.timing_text() == ""          # not started: nothing recorded

    mgr._started_at = time.monotonic() - 30.0
    mgr._calib_s = 4.1
    assert "calib 4.1s" in mgr.timing_text()

    mgr._map_s = 1.3
    assert "calib 4.1s" in mgr.timing_text()
    assert "map 1.3s" in mgr.timing_text()

    mgr._first_event_s = 4.7
    t = mgr.timing_text()
    assert t.index("calib") < t.index("map") < t.index("1st event")
    assert "1st event 4.7s" in t
    mgr.shutdown()


def test_source_status_suffix_renders_timing(make):
    from farever_companion.ui.dps_source_text import _timing_suffix
    mgr = make()
    assert _timing_suffix(mgr) == ""        # nothing recorded yet

    mgr._started_at = time.monotonic() - 30.0
    mgr._calib_s = 4.1
    mgr._map_s = 1.3
    mgr._first_event_s = 4.7
    suf = _timing_suffix(mgr)
    assert "calib 4.1s" in suf
    assert "map 1.3s" in suf
    assert "1st event 4.7s" in suf
    assert suf.startswith(" (") and suf.endswith(")")
    mgr.shutdown()


def test_source_status_names_the_live_bridge(make):
    from farever_companion.ui.dps_source_text import source_status
    mgr = make()
    mgr.ensure_started()
    mgr.set_mode("proxy")
    mgr.bridge.events_n = 7
    mgr.bridge._last_event_ts = time.monotonic()
    text, color = source_status(mgr)
    assert "combat bridge (proxy)" in text and "7 events" in text
    mgr.shutdown()


def test_source_status_reports_each_missing_dependency(make):
    from farever_companion.ui.dps_source_text import source_status, empty_hint
    mgr = make()
    mgr.ensure_started()

    mgr.set_mode("proxy")
    assert "Option 1" in source_status(mgr)[0]
    assert "Option 1" in empty_hint(mgr, "default")

    mgr.set_mode("injector")
    assert "Option 2" in source_status(mgr)[0]
    assert "Option 2" in empty_hint(mgr, "default")
    mgr.shutdown()


# ======================================================================
# bridge (core/dps_bridge.py) -- Options 1 (proxy) & 2 (injector)
# ======================================================================

MODES = ("proxy", "injector")


class _BridgeProc:
    """The bridge never needs the memory scanner, so has_scan stays off."""

    has_scan = False

    def __init__(self, loaded=(), option=None, proxy_on_disk=False):
        self._loaded = set(loaded)
        self._option = option
        self._proxy_on_disk = proxy_on_disk

    def is_module_loaded(self, name):
        return name in self._loaded

    def detect_combat_bridge(self):
        return {"option": self._option,
                "proxy_in_game_dir": self._proxy_on_disk,
                "status_summary": f"option {self._option}"}


def _bridge(proc=None, mode="proxy"):
    """A BridgeSource wired to a capture list instead of the real UDP socket."""
    events: list = []
    src = BridgeSource(proc or _BridgeProc(), emit=events.extend)
    src.set_mode(mode)
    return src, events


# --- payload contract (one parser for both options) ------------------------
@pytest.mark.parametrize("mode", MODES)
def test_damage_line_parses(mode):
    ev = parse_hook_event(
        '{"t":"hit","amt":1250.5,"crit":1,"kill":0,"sk":"Thunderclap",'
        '"src":"HeroPlayer","tgt":"BossMob","me":1}')
    assert ev is not None
    assert ev.amount == 1250.5
    assert ev.skill == "Thunderclap"
    assert ev.is_crit is True
    assert ev.is_kill is False
    assert ev.source_name == "HeroPlayer"
    assert ev.target_name == "BossMob"
    assert ev.is_me is True
    assert ev.is_heal is False


@pytest.mark.parametrize("mode", MODES)
def test_heal_line_prefers_the_amount_that_landed(mode):
    """Heals carry the raw amount (amt) and the effective one (landed)."""
    ev = parse_hook_event(
        '{"t":"heal","amt":450.0,"landed":400.0,"sk":"PrayerOfHealing",'
        '"src":"Priest","tgt":"Warrior","me":0}')
    assert ev is not None
    assert ev.amount == 400.0        # what actually landed
    assert ev.skill == "PrayerOfHealing"
    assert ev.source_name == "Priest"
    assert ev.target_name == "Warrior"
    assert ev.is_me is False
    assert ev.is_heal is True


@pytest.mark.parametrize("mode", MODES)
def test_heal_line_without_landed_uses_raw_amount(mode):
    ev = parse_hook_event('{"t":"heal","amt":450.0,"sk":"Regen","src":"You"}')
    assert ev is not None and ev.amount == 450.0


def test_hit_line_carries_the_per_hit_damage_type():
    """The bridge's short key and a hand-written long one both land on the
    event; an older DLL that omits them is untagged, never an error."""
    assert parse_hook_event(
        '{"t":"hit","amt":100,"sk":"Attack","aff":"Fire"}'
    ).affinity == "Fire"
    assert parse_hook_event(
        '{"t":"hit","amt":100,"sk":"Attack","affinity":"Magic"}'
    ).affinity == "Magic"
    assert parse_hook_event(
        '{"t":"hit","amt":100,"sk":"Attack"}'
    ).affinity == ""
    # What the rebuilt DLL actually sends for an untagged hit: the key is
    # always present (the C side emits it unconditionally) and empty.
    assert parse_hook_event(
        '{"t":"hit","amt":1234.5,"sk":"Sword_Base_Attack","src":"Hero",'
        '"tgt":"Boss","c":0,"k":0,"me":1,"aff":""}'
    ).affinity == ""
    # ...and the same line with a real type, as the DLL formats it.
    ev = parse_hook_event(
        '{"t":"hit","amt":1234.5,"sk":"Sword_Base_Attack","src":"Hero",'
        '"tgt":"Boss","c":0,"k":1,"me":1,"aff":"Physical"}')
    assert ev.affinity == "Physical" and ev.amount == 1234.5 and ev.is_kill
    # a recycled slot's junk (or a renamed field) is not a damage type
    for junk in ('"instStretch:y"', '"<null>"', '17', 'null'):
        ev = parse_hook_event(
            '{"t":"hit","amt":100,"sk":"Attack","aff":%s}' % junk)
        assert ev is not None and ev.affinity == "", junk


def test_hit_line_can_carry_the_targets_address():
    """The wire now names the target unit's ADDRESS (`taddr`).

    A target NAME cannot tell one training dummy from another, so a name-only
    wire folded a three-dummy yard onto one split row (live 2026-10-03). The
    address is what splits it per dummy, and it is optional for the same reason
    as `aff`: an older bridge omits it, and a missing address must degrade to
    name-keyed bucketing, never drop the hit.
    """
    # The short key the DLL emits, with the rest of the line as it formats it.
    ev = parse_hook_event(
        '{"t":"hit","amt":700.0,"sk":"Sword_Base_Attack","src":"You",'
        '"tgt":"Practice Dummy","taddr":140234567890,"c":0,"k":0,"me":1}'
    )
    assert ev is not None and ev.target_addr == 140234567890
    # a hand-written payload may use the long alias, in hex
    ev = parse_hook_event(
        '{"t":"hit","amt":1.0,"sk":"Attack","target_addr":"0x7ff6cafe1234"}'
    )
    assert ev is not None and ev.target_addr == 0x7ff6cafe1234
    # absent (an older bridge) is 0, never an error
    ev = parse_hook_event('{"t":"hit","amt":1.0,"sk":"Attack"}')
    assert ev is not None and ev.target_addr == 0
    # junk is not an address, and the hit still counts
    for junk in ('"nope"', 'null', '17.5', '""'):
        ev = parse_hook_event(
            '{"t":"hit","amt":100,"sk":"Attack","taddr":%s}' % junk)
        assert ev is not None and ev.target_addr == 0, junk
    assert parse_hook_event(
        '{"t":"hit","amt":100,"sk":"Attack","taddr":-5}'
    ).target_addr == 0


# --- the shipped binaries must speak that contract -------------------------
_BRIDGE_DIR = Path(__file__).resolve().parent.parent / "dps_bridge"

# Every native binary that builds a hit payload. All three now build from ONE
# hook core (hook/bridge_core.h), so a field cannot be added to the injector and
# missed by the proxies - tests/test_native_hook_core.py pins that at the source
# level. That is why "aff" shipped in farever_dps.dll alone on 2026-09-29: the
# injector's copy read the field and the proxies' copy did not, and the only
# coverage here asserted the PARSER accepts a hand-written line. These three are
# build output, so this remains the guard that the binaries were REBUILT from
# the shared core rather than left behind.
_HIT_EMITTERS = ("farever_dps.dll", "dinput8.dll", "userenv.dll")

_OUTGOING_HIT = (b'{"t":"hit","amt":%.1f,"sk":"%ls%s","src":"%ls",'
                 b'"tgt":"%ls","taddr":%llu,"c":%d,"k":%d,"me":%d,'
                 b'"aff":"%ls"}')
_INCOMING_HIT = (b'{"t":"hit","inc":1,"amt":%.1f,"sk":"%ls",'
                 b'"src":"%ls","tgt":"%ls","taddr":%llu,"c":%d,"k":%d,'
                 b'"me":%d,"aff":"%ls"}')

# The damage-capture liveness contract, in the same binaries. TWO counters
# ride the heartbeat and `core/dps_bridge._note_heartbeat` reads both:
# `hits` says the capture is on the live path, and `unnamed` says it can
# NAME what it hit. The DLL resolves names inside the game and sends the
# literal "Enemy" when both of its reads come back empty - which is
# indistinguishable, on the board and in the archive, from a build whose
# foe-name offsets stopped resolving unless the wire counts the miss.
#
# The one-shot line is what a dev-log reader sees instead of having to infer
# a dead hook from the absence of `[diag] WATCH` lines (2026-09-30: the
# armed onInflictDamage hook fired three times and was still read as
# silent).
#
# This is the TEMPLATE, byte for byte, because the assertion is about the
# bytes in the shipped binary: a stale binary is exactly the failure it
# exists to catch. `unnamed` was added on the wire after this literal was
# written, and the mismatch went unnoticed for a while because the literal
# was asserting on the PRECEDING template - the DLLs have carried the
# newer one all along while this file still asked for the older one. When a
# counter is added on the wire, add it here AND the reader in
# core/dps_bridge.py: a field carried by every heartbeat and read by
# nothing is the same silence this file keeps getting bitten by.
_HEARTBEAT = (b'{"t":"heartbeat","tick":%u,"hooks":4,"canary":%ld,'
              b'"hits":%ld,"unnamed":%ld}')
# Named separately so a failure says WHICH counter went missing, and who
# stops hearing about it. Each token appears exactly once in a binary - it
# is a single template - so a count of 2 would mean a second, diverging
# template had been compiled in beside it.
_HEARTBEAT_COUNTERS = {b'"hits":%ld': "_note_hits",
                       b'"unnamed":%ld': "_note_unnamed"}
_FIRST_HIT_LOG = (b"[hits] first damage event sent (%s findex %d, %.1f dmg%s) "
                  b"- the damage capture is on the live path")


@pytest.mark.parametrize("dll", _HIT_EMITTERS)
def test_every_shipped_binary_emits_the_damage_type_on_both_hit_branches(dll):
    """Both payload templates carry aff, in every shipped binary.

    Asserted against the binary's own format strings rather than a rendered
    line, so it proves the field is read and interpolated - and that no school
    is hardcoded, because the live vocabulary is open (schools and mechanic
    tags share this one field, so the DLL must not pick a default).
    """
    path = _BRIDGE_DIR / dll
    if not path.is_file():
        pytest.skip(f"dps_bridge/{dll} not present")
    blob = source_guard.read_bytes(path)

    assert blob.count(_OUTGOING_HIT) == 1, f"{dll}: outgoing hit lost aff"
    assert blob.count(_INCOMING_HIT) == 1, f"{dll}: incoming hit lost aff"
    assert b"Physical" not in blob, f"{dll}: a damage school is hardcoded"


@pytest.mark.parametrize("dll", _HIT_EMITTERS)
def test_every_shipped_binary_reports_what_the_damage_capture_sent(dll):
    """The hit counter is only real if the shipped bytes carry it.

    Its absence is how a working capture reads as a dead one: on 2026-09-30 the
    armed `ent.Unit.onInflictDamage` hook fired (190 outgoing, then 12 and 25
    incoming) yet its TEMP diag lines were named after the method while the
    probe's were named `[diag] WATCH <type>.<method>`, so a grep for the probe
    form found nothing. The permanent replacement names the capture in a
    one-shot log line and carries the running total on every heartbeat - both
    of which have to be IN the binaries, not only in bridge_core.h, because a
    stale binary is exactly the failure this asserts against.
    """
    path = _BRIDGE_DIR / dll
    if not path.is_file():
        pytest.skip(f"dps_bridge/{dll} not present")
    blob = source_guard.read_bytes(path)

    assert blob.count(_FIRST_HIT_LOG) == 1, f"{dll}: no first-hit capture line"
    assert blob.count(_HEARTBEAT) == 1, (
        f"{dll}: the shipped heartbeat template is not the one this "
        f"file pins - the binary is stale, or the wire grew a field "
        f"and this literal did not")
    for token, reader in _HEARTBEAT_COUNTERS.items():
        assert blob.count(token) == 1, (
            f"{dll}: heartbeat carries no {token.decode()} - "
            f"core/dps_bridge.{reader} reads that counter, and "
            f"nothing else reports it, so the DLL goes quiet with no "
            f"word anywhere")


@pytest.mark.parametrize("mode", MODES)
def test_kill_line_parses(mode):
    ev = parse_hook_event('{"t":"kill","src":"Mage","tgt":"Goblin","me":1}')
    assert ev is not None
    assert ev.is_kill is True
    assert ev.source_name == "Mage"
    assert ev.target_name == "Goblin"
    assert ev.is_me is True


@pytest.mark.parametrize("mode", MODES)
def test_kill_uid_folds_into_target(mode):
    """A kill line carries only the killed unit's id, under "uid"."""
    ev = parse_hook_event('{"t":"kill","uid":"Boss_Foo"}')
    assert ev is not None
    assert ev.is_kill is True
    assert ev.amount == 0.0
    assert ev.target_name == "Boss_Foo"
    assert ev.is_heal is False


@pytest.mark.parametrize("mode", MODES)
def test_malformed_lines_are_ignored(mode):
    assert parse_hook_event("") is None
    assert parse_hook_event("not json") is None
    assert parse_hook_event('{"t":"unknown_type"}') is None
    assert parse_hook_event('["not", "an", "object"]') is None


@pytest.mark.parametrize("mode", MODES)
def test_status_line_never_becomes_an_event(mode):
    assert parse_hook_event('{"t":"status","s":"table","ok":0,"m":"boom"}') is None


@pytest.mark.parametrize("mode", MODES)
def test_pet_hits_are_tagged(mode):
    ev = parse_hook_event('{"t":"hit","amt":10,"p":"Skeleton","sk":"Pet_Bite"}')
    assert ev is not None and ev.skill == "Pet_Bite (pet)"


# --- mode routing: Option 1 vs Option 2 -----------------------------------
def test_each_mode_reports_its_own_missing_dependency():
    """No DLL anywhere -> Option 1 wants the proxy, Option 2 wants an inject."""
    proxy, _ = _bridge(_BridgeProc(), mode="proxy")
    assert proxy.status() == "missing_proxy"

    inject, _ = _bridge(_BridgeProc(), mode="injector")
    assert inject.status() == "need_inject"


def test_detected_option_marks_the_matching_mode_ready():
    """detect_combat_bridge's `option` field is what selects the ready mode."""
    loading_proxy = _BridgeProc(option=1, proxy_on_disk=True)
    assert _bridge(loading_proxy, mode="proxy")[0].status() == "bridge_ready"
    assert _bridge(loading_proxy, mode="injector")[0].status() == "need_inject"

    loading_inject = _BridgeProc(option=2)
    assert _bridge(loading_inject, mode="injector")[0].status() == "bridge_ready"
    assert _bridge(loading_inject, mode="proxy")[0].status() == "missing_proxy"


def test_module_probe_fallback_when_detector_is_absent():
    """Without detect_combat_bridge we fall back to per-mode module names."""
    class _Bare(_BridgeProc):
        detect_combat_bridge = None

    proxy = _BridgeProc(loaded=("userenv.dll",))
    proxy.detect_combat_bridge = None
    assert _bridge(proxy, mode="proxy")[0].status() == "bridge_ready"

    inject = _BridgeProc(loaded=("farever_dps.dll",))
    inject.detect_combat_bridge = None
    assert _bridge(inject, mode="injector")[0].status() == "bridge_ready"


@pytest.mark.parametrize("proxy_name", PROXY_MODULES)
def test_module_probe_covers_every_shipped_proxy_name(proxy_name):
    """The fallback module probe must know every proxy we ship.

    A name added to the game-folder scan and to detect_combat_bridge but left
    out of this probe's list makes an installed, working proxy report
    "missing_proxy" whenever the detector returns nothing - that is how the
    retired winmm.dll was once missed.
    """
    probe = _BridgeProc(loaded=(proxy_name,))
    probe.detect_combat_bridge = None
    assert _bridge(probe, mode="proxy")[0].status() == "bridge_ready"


def test_proxy_name_lists_cannot_drift():
    """One list of proxy names, shared by the scanner, the scan and the probe."""
    from farever_companion.core import dps_bridge, proc
    from farever_companion.core.pe_imports import PROXY_DLL_NAMES

    assert dps_bridge.PROXY_MODULES == PROXY_DLL_NAMES
    assert proc.PROXY_DLL_NAMES is PROXY_DLL_NAMES
    # winmm.dll retired 2026-09-23: no shipped proxy loads at t=0 any more, so
    # the list leads with the names the game loads on every launch.
    assert "dinput8.dll" in PROXY_DLL_NAMES
    assert "winmm.dll" not in PROXY_DLL_NAMES


def test_source_name_names_the_selected_option():
    assert _bridge(mode="proxy")[0].source_name() == "combat bridge (proxy)"
    assert _bridge(mode="injector")[0].source_name() == "combat bridge (injector)"


def test_unknown_mode_falls_back_to_option_1():
    src, _ = _bridge(mode="nonsense")
    assert src.mode == "proxy"


# --- event intake ---------------------------------------------------------
def test_bridge_stamps_the_selected_capture_mode_on_events():
    src, events = _bridge(mode="proxy")
    src._ingest('{"t":"hit","amt":10,"src":"You","tgt":"Mob"}', 1.0)
    assert events[0].capture_mode == "proxy"


def test_datagram_events_are_forwarded_and_counted():
    src, events = _bridge()
    src._ingest('{"t":"hit","amt":10,"src":"You","tgt":"Mob"}', 1.0)
    assert events and events[0].amount == 10.0

    src._ingest('{"t":"hit","amt":5}\n{"t":"hit","amt":7}', 2.0)
    assert len(events) == 3
    assert src.status() == "live"          # events just arrived


def test_heartbeat_keeps_the_bridge_ready():
    src, events = _bridge()
    # The real payload carries the two liveness counters (frame canary and
    # damage-capture hit count); a newer DLL must never be parsed as an event.
    src._ingest('{"t":"heartbeat","tick":3,"canary":480,"hits":17}', 1.0)
    assert events == []                    # never a combat event
    assert src.heartbeat_alive() is True
    assert src.status() == "bridge_ready"


def test_first_ten_hits_are_logged_then_rolled_up():
    logged: list[str] = []
    src, _ = _bridge()
    src.log_line = logged.append
    for i in range(12):
        src._ingest('{"t":"hit","amt":1,"sk":"Sword"}', 1.0)
    assert sum(1 for line in logged if line.startswith("Combat Bridge: [HIT]")) == 10
    assert any("Live combat events streaming" in line for line in logged)


# --- hook-install status telemetry ("why is the bridge silent?") -----------
def _status(stage: str, ok: int, msg: str) -> dict:
    return {"t": "status", "s": stage, "ok": ok, "m": msg}


def test_status_telemetry_is_logged_and_healthy_run_stays_quiet():
    src, _ = _bridge()
    logs: list[str] = []
    src.log_line = logs.append
    src._ingest('{"t":"status","s":"boot","ok":1,"m":"bridge init start"}', 1.0)
    src._ingest('{"t":"status","s":"table","ok":1,"m":"functions_ptrs table located"}', 2.0)
    src._ingest('{"t":"status","s":"hook","ok":1,"m":"onInflictDamage findex 4865 -> OK"}', 3.0)
    src._ingest('{"t":"status","s":"mh_enable","ok":1,"m":"hooks armed - combat events stream over UDP"}', 4.0)
    assert any("Combat Bridge: hooks armed" in line for line in logs)
    assert not any("FAILED" in line for line in logs)
    assert src.bridge_issue() == ""


def test_status_telemetry_reports_a_table_miss():
    src, _ = _bridge()
    logs: list[str] = []
    src.log_line = logs.append
    src.handle_status(_status("boot", 1, "bridge init start"))
    src.handle_status(_status("table", 0,
        "functions_ptrs table NOT found after 50 scans "
        "(fmt.hdll anchors 33131/33132/35144 unmatched)"))
    assert "NOT found" in src.bridge_issue()
    assert any("FAILED" in line for line in logs)


def test_status_telemetry_shows_hook_failures_before_arm():
    src, _ = _bridge()
    src.handle_status(_status("boot", 1, "bridge init start"))
    src.handle_status(_status("hook", 0,
        "onInflictDamage findex 4865 -> MH_ERROR_NOT_EXECUTABLE"))
    assert "MH_ERROR_NOT_EXECUTABLE" in src.bridge_issue()


def test_a_refused_arm_is_reported_and_never_looks_like_a_clean_run():
    """The corroboration gate refuses when the statics walk and the heap sweep
    do not agree on the same functions_ptrs. Nothing is armed in that case, so no
    healthy mh_enable follows to close the window - the footer has to keep saying
    why the meter is empty, with the route that was silent in the message."""
    src, _ = _bridge()
    logs: list[str] = []
    src.log_line = logs.append
    src.handle_status(_status("boot", 1, "bridge init start"))
    src.handle_status(_status(
        "hunt", 0,
        "NOT ARMING: 0x000001F244003440 was confirmed by one discovery route only "
        "(statics walk cannot reach it); both must agree - see farever_dps.log"))
    issue = src.bridge_issue()
    assert "NOT ARMING" in issue
    assert "statics walk cannot reach it" in issue
    assert any("FAILED" in line for line in logs)


def test_status_telemetry_respects_the_session_boundary():
    """A failed OLD load never leaks into a healthy new one after a restart."""
    src, _ = _bridge()
    src.handle_status(_status("boot", 1, "old boot"))
    src.handle_status(_status("table", 0, "old table miss"))
    src.handle_status(_status("boot", 1, "new boot"))
    src.handle_status(_status("table", 1, "functions_ptrs table located"))
    src.handle_status(_status("mh_enable", 1, "hooks armed"))
    assert src.bridge_issue() == ""


# --- naming the damage pipeline (no manual log read) ------------------------
# The damage findex is POSITIONAL: every patch renumbers it (4932 -> 4936 on
# 2026-10-01 alone). A literal here does not go quietly stale - it fails on the
# next patch, and until then it tests the fallback path and calls it a pass.
# Read the number the app ships, which the manifest generator keeps in step
# with the game, so what is being checked is the NAMING and not the patch.
DAMAGE_FINDEX = HL_FINDEX_INFLICT_DAMAGE


def _arm_damage_hook(name: str = "onInflictDamage", findex: int = DAMAGE_FINDEX,
                     table: int = 0) -> list[str]:
    """The two `hook` status lines the DLL sends for one armed method."""
    return [
        f'{{"t":"status","s":"hook","ok":1,"m":"{name} findex {findex} -> OK"}}',
        (f'{{"t":"status","s":"hook","ok":1,"m":"{name} armed on table '
         f'{table} (0x000001b756161360)"}}'),
    ]


def test_the_activity_log_names_the_damage_pipeline_itself():
    """The companion reads the bridge's own lines and names the function the
    meter rides, so classifying the capture no longer needs a manual read of
    bridge_dev.log - which is exactly how a hook that fired three times was
    written down as silent on 2026-09-30 (its diag lines were `[diag]
    onInflictDamage`, the probe's were `[diag] WATCH ...`, and a grep for the
    second form found nothing).

    One line per DLL session, spoken when the heartbeat's hit counter proves a
    hit actually reached the capture.
    """
    src, _ = _bridge()
    logs: list[str] = []
    src.log_line = logs.append
    for line in _arm_damage_hook():
        src._ingest(line, 1.0)
    src._ingest('{"t":"heartbeat","tick":1,"canary":480,"hits":0}', 2.0)
    assert not any("damage pipeline live" in line for line in logs)

    src._ingest('{"t":"heartbeat","tick":2,"canary":540,"hits":1}', 3.0)
    src._ingest('{"t":"heartbeat","tick":3,"canary":600,"hits":7}', 4.0)
    named = [line for line in logs if "damage pipeline live" in line]
    assert len(named) == 1, "one line per session, not one per heartbeat"
    assert "ent.Unit.onInflictDamage" in named[0]     # the game-side name
    assert f"findex {DAMAGE_FINDEX}" in named[0] and "table 0" in named[0]
    assert src.hits_n == 7 and src.hits_reported is True
    # In a normal run that one line IS the story: the raw arming chatter is a
    # dev-run line (test_the_per_stage_chatter_is_a_dev_run_line), which is
    # the whole point - the classification arrives without reading a log.
    assert not any(f"findex {DAMAGE_FINDEX} -> OK" in line for line in logs)


def test_the_per_stage_chatter_is_a_dev_run_line(monkeypatch):
    """Read the log without drowning in it.

    A normal run keeps the lines that answer a question - the arm summary and
    the canary verdict - and drops the per-stage install chatter, so a user's
    Activity Log stays readable. ``run-log.bat``'s ``FAREVER_DEV_CRASH_LOG``
    is what buys the full stream back for a developer. Failures are never
    gated (test_status_telemetry_reports_a_table_miss pins that).
    """
    from farever_companion.core.bridge_log import DEV_LOG_ENV

    stream = [
        ('{"t":"status","s":"boot","ok":1,"m":"bridge init start"}', 1.0),
        ('{"t":"status","s":"table","ok":1,'
         '"m":"functions_ptrs table located"}', 2.0),
        *[(line, 3.0) for line in _arm_damage_hook()],
        ('{"t":"status","s":"mh_enable","ok":1,"m":"hooks armed"}', 5.0),
        ('{"t":"status","s":"canary","ok":1,"m":"canary ticked"}', 6.0),
    ]

    def _run() -> list[str]:
        src, _ = _bridge()
        logs: list[str] = []
        src.log_line = logs.append
        for line, at in stream:
            src._ingest(line, at)
        return logs

    logs = _run()
    assert not any(f"findex {DAMAGE_FINDEX}" in line for line in logs)
    assert not any("bridge init start" in line for line in logs)
    assert not any("functions_ptrs table located" in line for line in logs)
    assert any("hooks armed" in line for line in logs)      # the arm summary
    assert any("canary ticked" in line for line in logs)    # the canary verdict

    monkeypatch.setenv(DEV_LOG_ENV, "run.log")
    dev_logs = _run()
    assert any(f"findex {DAMAGE_FINDEX} -> OK" in line for line in dev_logs)
    assert any("bridge init start" in line for line in dev_logs)


def test_a_heartbeat_without_the_hit_counter_never_claims_a_live_capture():
    """A DLL older than the counter: nothing on the wire says the capture ever
    fired, so nothing is claimed. An armed-and-silent bridge must not be
    described as live by this line."""
    src, _ = _bridge()
    logs: list[str] = []
    src.log_line = logs.append
    for line in _arm_damage_hook():
        src._ingest(line, 1.0)
    src._ingest('{"t":"heartbeat","tick":1,"canary":480}', 2.0)
    assert not any("damage pipeline live" in line for line in logs)
    assert src.hits_reported is False


def test_an_unexpected_arming_is_not_named_as_the_method_we_expect():
    """The line names what the DLL armed, never what this build assumes. A
    future DLL whose damage hook is another findex gets the honest generic
    line - spelling out the old method there would be the 2026-09-30 mistake
    in reverse."""
    src, _ = _bridge()
    logs: list[str] = []
    src.log_line = logs.append
    for line in _arm_damage_hook(name="recordsDamage", findex=9999, table=1):
        src._ingest(line, 1.0)
    src._ingest('{"t":"heartbeat","tick":1,"hits":4}', 2.0)
    named = [line for line in logs if "damage pipeline live" in line]
    assert len(named) == 1
    assert "ent.Unit.onInflictDamage" not in named[0]


def test_each_dll_session_names_its_own_capture():
    """A reload (proxy install, re-attach, game restart) is a new DLL session,
    and its own capture facts - so the line explains the run it belongs to
    instead of being suppressed by the previous one."""
    src, _ = _bridge()
    logs: list[str] = []
    src.log_line = logs.append
    boot = '{"t":"status","s":"boot","ok":1,"m":"farever_dps hook bridge init start"}'
    src._ingest(boot, 1.0)
    for line in _arm_damage_hook():
        src._ingest(line, 2.0)
    src._ingest('{"t":"heartbeat","tick":1,"hits":2}', 3.0)
    src._ingest(boot, 4.0)
    for line in _arm_damage_hook():
        src._ingest(line, 5.0)
    src._ingest('{"t":"heartbeat","tick":1,"hits":3}', 6.0)
    assert sum(1 for line in logs if "damage pipeline live" in line) == 2


# --- per-frame canary: "armed but silent" gets a name ----------------------
def test_a_silent_canary_after_a_healthy_arm_reaches_the_issue_line():
    """Every hook said OK, yet the per-frame detour never fired: the bridge
    must say so, instead of a healthy mh_enable closing the window on it."""
    src, _ = _bridge()
    logs: list[str] = []
    src.log_line = logs.append
    src.handle_status(_status("boot", 1, "bridge init start"))
    src.handle_status(_status("mh_enable", 1, "1 table(s): T0[dmg,kill,fx,hp]"))
    src.handle_status(_status(
        "canary", 0,
        "per-frame canary silent on every armed candidate table - hooks are "
        "armed on code the game does not execute (armed but silent bridge)"))
    issue = src.bridge_issue()
    assert "canary silent" in issue
    assert any("FAILED" in line for line in logs)


def test_a_later_healthy_canary_clears_an_earlier_silence_report():
    """A long load can keep the frame loop away for a while; when the canary
    starts firing the bridge is healthy again and the alarm must clear."""
    src, _ = _bridge()
    src.handle_status(_status("boot", 1, "bridge init start"))
    src.handle_status(_status("mh_enable", 1, "hooks armed"))
    src.handle_status(_status("canary", 0, "per-frame canary silent on every "
                                             "armed candidate table"))
    src.handle_status(_status("canary", 1, "per-frame canary on candidate "
                                             "table 0 started firing after ~30s"))
    assert src.bridge_issue() == ""


def test_a_canary_silence_never_leaks_across_a_restart():
    src, _ = _bridge()
    src.handle_status(_status("boot", 1, "old boot"))
    src.handle_status(_status("mh_enable", 1, "hooks armed"))
    src.handle_status(_status("canary", 0, "per-frame canary silent"))
    src.handle_status(_status("boot", 1, "new boot"))
    src.handle_status(_status("mh_enable", 1, "hooks armed"))
    src.handle_status(_status("canary", 1, "per-frame canary fired on table 0"))
    assert src.bridge_issue() == ""


def test_emit_failure_never_kills_the_listener():
    """A raising log sink must not take the UDP loop down with it."""
    src, events = _bridge()
    src.log_line = lambda _msg: (_ for _ in ()).throw(RuntimeError("sink gone"))
    src._ingest('{"t":"hit","amt":3}', 1.0)
    assert events and events[0].amount == 3.0


# --- the bridge port is exclusive (one receiver, or none) -----------------

def test_the_listener_never_shares_the_bridge_port():
    """No SO_REUSEADDR on ANY bridge socket, and no wildcard capture bind.

    On Windows - unlike POSIX - SO_REUSEADDR lets a second process bind a port
    that is already bound or LISTENING, and the loser silently receives nothing.
    That bit twice: first the UDP capture socket (a monitor stole every combat
    event and the Top DPS meter read empty), then the TCP debug relay, where a
    second FareverPal took subscriber connections and you would have been
    reading another process's stream while believing you were reading the DLL.

    Module-wide on purpose: there is no legitimate use left to exempt. An
    earlier version scoped this to _ipc_loop because the relay set the flag,
    which is exactly how the second bug survived - the exemption was the bug.
    """
    tree = ast.parse(source_guard.read_text(dps_bridge.__file__, encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if ast.unparse(node.func).endswith("setsockopt"):
            if any("SO_REUSEADDR" in ast.unparse(a) for a in node.args):
                offenders.append(ast.unparse(node))
    assert not offenders, f"bridge sockets must be exclusive, found: {offenders}"

    loop = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_ipc_loop")
    wildcards = [ast.unparse(n) for n in ast.walk(loop)
                 if isinstance(n, ast.Call) and ast.unparse(n.func).endswith("bind")
                 and any("0.0.0.0" in ast.unparse(a) for a in n.args)]
    assert not wildcards, f"capture must bind loopback only: {wildcards}"


def test_a_second_relay_cannot_hijack_the_relay_port():
    """A second relay must fail to bind, loudly - not share the port.

    This is the TCP twin of the capture-socket rule, and it is what a second
    FareverPal instance (or a leftover one) would otherwise do: silently take
    every subscriber connection.
    """
    import socket
    from farever_companion.core.dps_bridge import LineRelay
    port = _free_port()
    first = LineRelay(port=port)
    assert first.start() is True
    try:
        second = LineRelay(port=port)
        assert second.start() is False, "a second relay must not bind the same port"
        assert second.active is False
    finally:
        first.stop()


def test_the_monitor_names_skills_the_way_the_app_does():
    """The monitor must not be the one place a skill id stays untranslated.

    The app resolves ids through data/names.py ('Priest_Prayer_Smite' ->
    'Prayer: Smite') and deliberately renders an unreadable skill as
    "Unknown", never as a basic attack (DpsTracker._skill_labels). The monitor
    did neither: it printed the raw field and defaulted to "Attack" - so a DLL
    that stopped sending skill ids looked like a character auto-attacking, and
    the raw id (the thing under suspicion) was never on screen.
    """
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "dps_bridge" / "monitor_bridge.py"
    spec = importlib.util.spec_from_file_location("_fv_monitor_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    resolved = mod.skill_label("Priest_Prayer_Smite")
    assert "Prayer: Smite" in resolved
    assert "Priest_Prayer_Smite" in resolved, "raw id must stay visible"

    # A missing skill must NOT be dressed up as a real auto-attack.
    assert mod.skill_label("") == "Unknown"
    assert mod.skill_label(None) == "Unknown"
    assert mod.skill_label("?") == "Unknown"

    line = mod.Printer().render(
        '{"t":"hit","amt":88.0,"src":"Priest"}', 1)
    assert "Unknown" in line and "Attack" not in line


def _load_monitor():
    import importlib.util
    path = Path(__file__).resolve().parents[1] / "dps_bridge" / "monitor_bridge.py"
    spec = importlib.util.spec_from_file_location("_fv_monitor_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_running_app_without_a_relay_does_not_say_start_the_app():
    """"Start the app" is a dead end when the app is already running.

    The monitor falls through to DIRECT when no relay answers, cannot bind,
    and used to print exactly that - telling the user to do the thing they had
    already done. The real state is an app process that predates the relay (or
    has it switched off), which needs a restart, not a launch. This cost a
    debugging session: the app was running the whole time.
    """
    mod = _load_monitor()
    holders = ["PID 1 [FareverPal.exe]: the FareverPal companion app - its "
               "bridge listener takes the traffic, so this monitor sees nothing"]
    mod._connect_relay = lambda timeout=1.0: None          # no relay answering
    advice = "\n".join(mod._relay_advice(holders))
    assert "IS running" in advice
    assert "Restart" in advice
    assert "start the app" not in advice.lower().replace("start the app first", "")


def test_no_relay_advice_when_the_holder_is_not_the_app():
    """A foreign holder is a different problem with a different fix."""
    mod = _load_monitor()
    mod._connect_relay = lambda timeout=1.0: None
    assert mod._relay_advice(["PID 9 [something.exe]: an unrecognised process"]) == []


def test_no_relay_advice_when_the_relay_is_actually_up():
    """A stale answer must not send the user to restart a working setup."""
    mod = _load_monitor()
    mod._connect_relay = lambda timeout=1.0: object()
    holders = ["PID 1 [FareverPal.exe]: the FareverPal companion app"]
    assert mod._relay_advice(holders) == []


def test_closing_a_subscriber_sends_fin_not_rst():
    """shutdown(SHUT_WR) first, so a subscriber reads EOF instead of a reset.

    Measured on this machine, which is why the flood is here: with unread data
    queued, a bare close() makes Windows answer the peer with RST
    (WSAECONNRESET 10054); shutdown(SHUT_WR) first yields a clean EOF. With an
    EMPTY queue both paths give EOF, so the flood is load-bearing - without it
    this assertion passes against the unfixed code too.

    Scope note: the monitor never writes to the relay, so this is the defensive
    half of the fix. The 10054 the user actually hit came from the app process
    going away, which resets the connection no matter what the server does -
    that is handled by the monitor catching OSError and reconnecting.
    """
    import socket
    from farever_companion.core.dps_bridge import _close_client
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    client = socket.create_connection(("127.0.0.1", port), timeout=2.0)
    client.settimeout(2.0)
    conn, _ = srv.accept()
    try:
        client.sendall(b"x" * 200_000)   # lands unread in conn's receive queue
        time.sleep(0.3)
        _close_client(conn)
        try:
            trailing = client.recv(4096)
        except (ConnectionResetError, ConnectionAbortedError) as e:
            pytest.fail(f"subscriber must see a clean EOF, got {e!r}")
        assert trailing == b"", "expected EOF"
    finally:
        client.close()
        srv.close()


def test_a_lost_port_is_reported_with_the_holder_not_swallowed():
    """A failed bind must reach the Activity Log, naming who holds the port.

    It used to be a log.debug nobody reads, which is why an empty DPS meter had
    no visible cause at all.
    """
    src, _ = _bridge()
    lines: list[str] = []
    src.log_line = lines.append
    src._log_port_clash(OSError("address already in use"))
    assert len(lines) == 1
    text = lines[0]
    assert "CANNOT listen" in text
    assert str(dps_bridge.UDP_PORT) in text
    assert "DPS will stay empty" in text


def test_port_clash_spam_is_throttled():
    """The retry loop runs every second; the warning must not."""
    src, _ = _bridge()
    lines: list[str] = []
    src.log_line = lines.append
    src._log_port_clash(OSError("busy"))
    src._log_port_clash(OSError("busy"))
    assert len(lines) == 1
    src._port_clash_ts -= 31.0        # window elapsed -> speak up again
    src._log_port_clash(OSError("busy"))
    assert len(lines) == 2


def test_port_holder_pids_finds_a_live_udp_listener():
    """Round-trips against a real socket, so the netstat column index is pinned.

    UDP rows have no State column ('UDP  127.0.0.1:N  *:*  PID' = 4 fields);
    reading the wrong field reports "nobody" and sends the user hunting the
    wrong process. Our own PID is the one holding the probe, so the expected
    answer is exact.
    """
    import os
    import socket
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        assert str(os.getpid()) in dps_bridge.port_holder_pids(port)
    finally:
        probe.close()


def test_port_holder_pids_is_empty_for_a_free_port():
    # High port nothing will hold; a held one would make this flaky.
    assert dps_bridge.port_holder_pids(65001) == []


def test_port_clash_names_the_holder_rather_than_guessing(monkeypatch):
    """A PID alone is not actionable — the two rivals need different fixes.

    The first version hardcoded "most likely the companion app" and was wrong
    in the exact case that mattered: a *stale monitor* held the port, so it
    told the user to close the one process that was not holding it, while the
    real culprit sat there. Both rivals must be named for what they are.
    """
    monkeypatch.setattr(dps_bridge, "port_holder_pids", lambda _p: ["111", "222"])
    monkeypatch.setattr(dps_bridge, "_process_details", lambda pids: {
        "111": ("python.exe", r"C:\repo\.venv\Scripts\python.exe dps_bridge\monitor_bridge.py"),
        "222": ("FareverPal.exe", r"C:\Games\FareverPal.exe"),
    })
    lines = dps_bridge.describe_port_clash(49152)
    assert len(lines) == 2
    stale = next(x for x in lines if x.startswith("PID 111"))
    app = next(x for x in lines if x.startswith("PID 222"))
    assert "stale copy of this monitor" in stale
    assert "companion app" in app


def test_port_clash_falls_back_when_the_process_is_unknown(monkeypatch):
    """An unidentifiable holder still gets a line — silence is what hurt."""
    monkeypatch.setattr(dps_bridge, "port_holder_pids", lambda _p: ["333"])
    monkeypatch.setattr(dps_bridge, "_process_details", lambda pids: {})
    lines = dps_bridge.describe_port_clash(49152)
    assert len(lines) == 1 and "PID 333" in lines[0]


# --- debug relay: watch the DLL while the app keeps the meter --------------

def _free_port() -> int:
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def test_relay_is_on_for_source_runs_and_opt_in_only_when_frozen(monkeypatch):
    """Debug affordance, not a shipped listener.

    A source-tree run is where debugging happens, so the relay is on there. A
    frozen build must not open a loopback socket nobody asked for — it takes an
    explicit FAREVER_BRIDGE_RELAY=1.
    """
    monkeypatch.delenv(dps_bridge.RELAY_ENV, raising=False)
    monkeypatch.setattr(dps_bridge, "is_frozen", lambda: False)
    assert dps_bridge.relay_enabled() is True
    monkeypatch.setattr(dps_bridge, "is_frozen", lambda: True)
    assert dps_bridge.relay_enabled() is False
    monkeypatch.setenv(dps_bridge.RELAY_ENV, "1")
    assert dps_bridge.relay_enabled() is True
    monkeypatch.setenv(dps_bridge.RELAY_ENV, "off")
    assert dps_bridge.relay_enabled() is False


def test_the_relay_lets_two_tools_watch_one_dll_stream(monkeypatch):
    """The whole point: the app keeps 49152 AND a monitor sees every line.

    Sending one datagram must reach both the app's own event pipeline and an
    attached subscriber, while a second direct bind on the same port still
    fails. That last part is the regression that started all this: if it ever
    succeeds, a monitor can once again starve the meter and the fix is void.
    """
    import socket
    import time
    udp_port, relay_port = _free_port(), _free_port()
    monkeypatch.setattr(dps_bridge, "UDP_PORT", udp_port)
    monkeypatch.setenv(dps_bridge.RELAY_ENV, "1")

    events: list = []
    src = BridgeSource(proc=_BridgeProc(), emit=events.extend)
    src._relay = dps_bridge.LineRelay(port=relay_port)
    src.start()
    try:
        assert src.relay_active is True
        client = socket.create_connection(("127.0.0.1", relay_port), timeout=2.0)
        client.settimeout(2.0)
        try:
            assert "attached" in client.recv(4096).decode("utf-8", "replace")

            tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            payload = b'{"t":"hit","amt":2500.0,"src":"Priest"}'
            # The UDP listener binds inside `src.start()`'s worker thread, so
            # it can still be unbound for the first instant after start()
            # returns. UDP has no retransmit, so a single datagram sent into
            # that window is dropped and the meter looks dead (it passed 2 of 3
            # full runs and failed the third, 2026-10-05). The DLL sends a
            # continuous stream, so resend within the deadline the way the real
            # feed does instead of betting the whole test on one packet.
            deadline = time.time() + 3.0
            while time.time() < deadline and not events:
                tx.sendto(payload, ("127.0.0.1", udp_port))
                time.sleep(0.05)
            tx.close()
            assert events, "app must still receive its own capture"
            assert events[0].amount == 2500.0

            seen = b""
            sub_deadline = time.time() + 3.0
            while b"\n" not in seen and time.time() < sub_deadline:
                seen += client.recv(4096)
            assert b'"amt":2500.0' in seen, "subscriber must see the raw line"

            # And the port is still exclusive — one receiver, not two.
            rival = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            with pytest.raises(OSError):
                rival.bind(("127.0.0.1", udp_port))
            rival.close()
        finally:
            client.close()
    finally:
        src.stop()
    assert src.relay_active is False


def test_a_dead_subscriber_never_stalls_the_capture_listener():
    """A debug tap must not be able to jam the meter.

    The subscriber is connected but never reads, so a blocking send would
    wedge the UDP loop and take the DPS feed down with it. A short send timeout
    plus drop is the guarantee.
    """
    import socket
    import time
    relay_port = _free_port()
    relay = dps_bridge.LineRelay(port=relay_port)
    assert relay.start() is True
    try:
        client = socket.create_connection(("127.0.0.1", relay_port), timeout=2.0)
        client.recv(4096)          # greeting, then never read again
        time.sleep(0.1)
        started = time.monotonic()
        for _ in range(50):
            relay.broadcast('{"t":"heartbeat","tick":1}')
        assert time.monotonic() - started < 2.0, "broadcast blocked on a dead client"
        client.close()
    finally:
        relay.stop()
    assert relay.clients == 0


def test_the_relay_forwards_lines_verbatim_before_parsing():
    """Subscribers debug the DLL, so they must see what it sent - not what the
    app's parser made of it. A line the parser would reject still arrives."""
    import socket
    relay_port = _free_port()
    relay = dps_bridge.LineRelay(port=relay_port)
    assert relay.start() is True
    try:
        client = socket.create_connection(("127.0.0.1", relay_port), timeout=2.0)
        client.settimeout(2.0)
        client.recv(4096)
        relay.broadcast("this is not json at all")
        assert b"not json" in client.recv(4096)
        client.close()
    finally:
        relay.stop()


# ======================================================================
# memory (core/dps_memory.py) -- Option 3, the DLL-free scanner
# ======================================================================

class _MemoryProc:
    def __init__(self, has_scan=True):
        self.has_scan = has_scan


class _Reader:
    """Stands in for DamageReader: same private fields, same public surface."""

    def __init__(self):
        self._type_ptr = None
        self._result_type_ptr = None
        self._heal_type_ptr = None
        self._ranges_ready = False
        self._diag: dict = {}
        self.my_hero = None
        self.poll_events: list = []
        # Blind-rescue arm (mirrors DamageReader._force_wide_until:
        # force_wide_rehunt() sets it, refresh_ranges() consumes it). A rescue
        # that came up EMPTY re-arms itself on `_force_retry_after`, so the
        # pair is what the poller's due check reads.
        self._force_wide_until = 0.0
        self._force_retry_after = 0.0
        self._blind_rescue_s = 0.0
        # Displays the last wide sweep found: -1 none this pass, 0 empty, >0 a
        # re-anchor (the poller logs recovery latency only on >0).
        self._last_sweep_hits = -1
        self.in_combat = False
        self.calls = {"find_type": 0, "find_result_type": 0, "find_heal_type": 0,
                      "refresh_ranges": 0, "poll": 0}

    @property
    def type_ptr(self):
        return self._type_ptr

    def find_type(self):
        self.calls["find_type"] += 1
        self._type_ptr = 0x1000

    def find_result_type(self):
        self.calls["find_result_type"] += 1
        self._result_type_ptr = 0x2000

    def find_heal_type(self):
        self.calls["find_heal_type"] += 1
        self._heal_type_ptr = 0x3000

    def force_wide_rehunt(self, why: str = "") -> None:
        """Arm a blind-rescue sweep (mirrors the real DamageReader)."""
        self._force_wide_until = time.monotonic() + 10.0
        self._force_retry_after = 0.0
        self._blind_rescue_s = 0.0

    def rescue_sweep_due(self) -> bool:
        """Mirrors DamageReader: armed AND past its retry deadline."""
        now = time.monotonic()
        return self._force_wide_until > now and now >= self._force_retry_after

    def refresh_ranges(self):
        self.calls["refresh_ranges"] += 1
        self._ranges_ready = True
        self._force_wide_until = 0.0   # the wide sweep consumes the arm
        return 3

    def poll(self):
        self.calls["poll"] += 1
        evs, self.poll_events = self.poll_events, []
        return evs


def _source(proc=None, reader=None, **kw):
    events: list = []
    src = MemorySource(proc or _MemoryProc(), emit=events.extend, **kw)
    if reader is not None:
        src.reader = reader
        src._started = True
        src._started_at = time.monotonic()
    return src, events


def _run_loop(src, seconds=0.25):
    """Run the real poll loop briefly, then stop it."""
    src._started = True
    src._started_at = time.monotonic()
    t = threading.Thread(target=src._loop, daemon=True)
    t.start()
    time.sleep(seconds)
    src._stop = True
    t.join(timeout=2.0)


def test_option3_forbids_whole_process_rescans():
    """Live Option 3 uses targeted ranges only; a wide rescue scan contends
    with the model's native process reads and makes the UI look hung."""
    src, _ = _source()
    reader = src.ensure_reader()
    assert reader._allow_wide_type_scan is False
    assert reader._allow_wide_memory_scan is True
    assert reader._incremental_wide_scan is True


# --- status phases --------------------------------------------------------
def test_noscan_without_the_memory_scanner():
    """Option 3 needs the native scanner; without it the mode cannot capture."""
    src, _ = _source(_MemoryProc(has_scan=False))
    assert src.status() == "noscan"


def test_off_before_started():
    src, _ = _source()
    assert src.status() == "off"


def test_locating_until_the_type_is_found():
    reader = _Reader()
    src, _ = _source(reader=reader)
    assert src.status() == "locating"
    assert src.located_pointers() == (None, None)

    src._locate_running = True
    src._locate_started = time.monotonic() - 33.0
    src._locate_attempts = 1                      # scan 2 in progress
    prog = src.locate_progress()
    assert "2" in prog and "33" in prog

    reader._type_ptr = 0x1000
    reader._result_type_ptr = 0x2000
    assert src.status() == "mapping"
    src._locate_running = False
    assert src.locate_progress() == ""


def test_live_then_silent_once_mapped():
    reader = _Reader()
    reader._type_ptr, reader._result_type_ptr = 0x1000, 0x2000
    reader._ranges_ready = True
    src, _ = _source(reader=reader)

    assert src.status() == "silent"               # mapped, nothing yet
    src._last_event_ts = time.monotonic()
    assert src.status() == "live"
    assert src.had_events() is True

    src._last_event_ts = time.monotonic() - 60.0
    assert src.status() == "silent"
    assert src.had_events() is False


def test_located_pointers_read_the_public_type_property():
    reader = _Reader()
    reader._type_ptr, reader._result_type_ptr = 0x7FFC1234, 0x7FFC5678
    src, _ = _source(reader=reader)
    assert src.located_pointers() == (0x7FFC1234, 0x7FFC5678)


def test_damage_stale_tracks_found_but_undecoded_displays():
    reader = _Reader()
    src, _ = _source(reader=reader)
    assert src.damage_stale is False
    assert src.diagnostics() == {}

    reader._diag = {"displays": 12, "ok": 0, "no_res": 11, "bad_amount": 1}
    assert src.damage_stale is True
    assert src.diagnostics()["displays"] == 12


# --- calibration driver ---------------------------------------------------
def test_first_derive_locates_every_type_and_records_calibration():
    reader = _Reader()
    src, _ = _source(reader=reader)
    src._started_at = time.monotonic() - 4.1

    src._calibrate(reader, last_derive=0.0, now=time.monotonic())

    assert reader.calls["find_type"] == 1
    assert reader.calls["find_result_type"] == 1
    assert reader.calls["find_heal_type"] == 1
    assert src.calib_s > 0.0
    assert src._locate_running is False


def test_mapped_reader_re_derives_on_the_healthy_cadence():
    reader = _Reader()
    src, _ = _source(reader=reader)
    reader._type_ptr, reader._result_type_ptr = 0x1000, 0x2000
    reader._ranges_ready = True

    now = time.monotonic()
    src._calibrate(reader, last_derive=now, now=now)
    assert reader.calls["refresh_ranges"] == 0     # not due yet

    src._calibrate(reader, last_derive=now - dps_memory.DERIVE_HEALTHY_S - 1, now=now)
    assert reader.calls["refresh_ranges"] == 1


def test_first_range_map_records_its_duration():
    reader = _Reader()
    src, _ = _source(reader=reader)
    src._refresh_timed(reader)
    assert src.map_s > 0.0
    assert src.mapping is False


def test_combat_hint_keeps_hot_rederive_cadence_mid_fight():
    """A live fight with NO decoded events must still re-derive at combat
    cadence. The old 6 s event-recency window collapsed the moment the reader
    went deaf mid-fight: no events -> "not hot" -> up to DERIVE_HEALTHY_S
    (300 s) without a re-derive, so the fight's remaining hits burst in when
    the fight ended (live 2026-09-18: a 0.0 s "fight" holding 81 hits, and a
    44 s boss fight streaming in ~6 bursts)."""
    reader = _Reader()
    src, _ = _source(reader=reader, combat_hint=lambda: True)
    reader._type_ptr, reader._result_type_ptr = 0x1000, 0x2000
    reader._ranges_ready = True
    src._last_event_ts = 0.0        # deaf: no decoded event anywhere near now

    now = time.monotonic()
    src._calibrate(reader, last_derive=now, now=now)
    assert reader.calls["refresh_ranges"] == 0     # not due yet

    src._calibrate(reader, last_derive=now - dps_memory.DERIVE_HOT_S - 0.1, now=now)
    assert reader.calls["refresh_ranges"] == 1     # combat cadence, not HEALTHY


def test_no_combat_hint_keeps_the_quiet_cadence():
    """Between fights the healthy 300 s cadence still applies: the combat
    hint must not force constant re-mapping while the world is calm."""
    reader = _Reader()
    src, _ = _source(reader=reader, combat_hint=lambda: False)
    reader._type_ptr, reader._result_type_ptr = 0x1000, 0x2000
    reader._ranges_ready = True
    src._last_event_ts = 0.0

    now = time.monotonic()
    src._calibrate(reader, last_derive=now - dps_memory.DERIVE_HOT_S - 0.1, now=now)
    assert reader.calls["refresh_ranges"] == 0     # quiet: not due for a long while


def test_recalibrate_forces_an_immediate_derive():
    """recalibrate() must not wait out the quiet LULL cadence."""
    reader = _Reader()
    src, _ = _source(reader=reader)
    reader._type_ptr, reader._result_type_ptr = 0x1000, 0x2000
    reader._ranges_ready = True

    now = time.monotonic()
    src._calibrate(reader, last_derive=now, now=now)
    assert reader.calls["refresh_ranges"] == 0

    src.recalibrate()
    src._calibrate(reader, last_derive=now, now=now)
    assert reader.calls["refresh_ranges"] == 1


# --- engine arbitration (one capture at a time) ---------------------------
@pytest.fixture
def _fast_loop(monkeypatch):
    monkeypatch.setattr(dps_memory, "START_SETTLE_S", 0.0)
    monkeypatch.setattr(dps_memory, "POLL_IDLE", 0.01)
    monkeypatch.setattr(dps_memory, "POLL_IN_COMBAT", 0.01)


def test_loop_parks_while_another_option_owns_the_capture(_fast_loop):
    reader = _Reader()
    src, _ = _source(reader=reader, active=lambda: False)
    _run_loop(src)
    assert reader.calls["poll"] == 0


def test_loop_never_yields_to_the_bridge(_fast_loop):
    """The memory loop is DLL-free: it polls whenever it owns the capture,
    regardless of any leftover bridge traffic (the coordinator stops the
    bridge engine on mode switch, so there is nothing to yield to)."""
    reader = _Reader()
    src, events = _source(reader=reader, active=lambda: True)
    reader._type_ptr, reader._result_type_ptr = 0x1000, 0x2000
    reader._ranges_ready = True
    reader.poll_events = [DamageEvent(amount=42.0, skill="Sword")]
    _run_loop(src)
    assert reader.calls["poll"] > 0
    assert [e.amount for e in events] == [42.0]


def test_loop_polls_and_emits_when_it_owns_the_capture(_fast_loop):
    reader = _Reader()
    src, events = _source(reader=reader, active=lambda: True)
    reader._type_ptr, reader._result_type_ptr = 0x1000, 0x2000
    reader._ranges_ready = True
    reader.poll_events = [DamageEvent(amount=42.0, skill="Sword")]

    _run_loop(src)

    assert reader.calls["poll"] > 0
    assert [e.amount for e in events] == [42.0]
    assert src.status() == "live"


def test_loop_refreshes_the_hero_hint_for_incoming_flags(_fast_loop):
    reader = _Reader()
    src, _ = _source(reader=reader, active=lambda: True,
                     hero_hint=lambda: 0xBEEF)
    reader._type_ptr, reader._result_type_ptr = 0x1000, 0x2000
    reader._ranges_ready = True
    _run_loop(src)
    assert reader.my_hero == 0xBEEF


# ---------------------------------------------------------------------------
# the injector waits for player location before starting the DLL
# ---------------------------------------------------------------------------

def test_injector_waits_for_player_location_without_a_ui_thread_map_poll():
    """Option 2 is requested at attach but launched only after the worker
    resolves the player. The locate worker is already off the UI thread; this
    must not be replaced with a scene.map_id() poll on the UI thread.

    The launch itself sits one call deeper now: the located transition (and
    the inject watchdog's retry) share ``_launch_injector``, which is the only
    caller of ``_try_launch_hook``."""
    pytest.importorskip("PySide6")
    import farever_companion.ui.game_attach as ga

    src = source_guard.read_text(ga.__file__, encoding="utf-8")
    assert "self._hook_pending = True" in src
    assert "Waiting for player location before injecting" in src
    assert "getattr(self, \"_hook_pending\", False)" in src
    assert "self._launch_injector()" in src
    assert "_try_launch_hook(self.proc.pid, force=force, log_cb=self.log.emit" in src
    assert "map_id()" not in src


def test_inject_launch_candidates_never_name_the_uninjector(monkeypatch, tmp_path):
    """The launch list must contain ONLY farever_dps.exe, and the frozen
    bundle's copy must sort FIRST.

    build_all.bat used to publish the injector under two names, and injector.c
    read its own filename to decide inject-vs-uninject, so the two copies were
    different programs. The old `candidates.insert(0, ...)` over
    ("farever_dps.exe", "farever_uninject.exe") put the UNINJECTOR at index 0,
    so Option 2 ran the removal path, printed "Nothing to uninject", and left
    the DLL unloaded - the meter then sat at status=need_inject with zero
    events for the whole session, which reads as a dead injector rather than a
    wrong binary. See the "Boss engaged with no damage being read" line. The
    filename dispatch has since been removed from injector.c, so what remains
    is only "the launch list ran a binary nobody built for it" - which is what
    this pins."""
    pytest.importorskip("PySide6")
    import farever_companion.ui.game_attach as ga

    assert ga._INJECT_EXE_NAMES == ("farever_dps.exe",)

    # Frozen bundle: only the MEIPASS copies exist, and the first entry is
    # the one _try_launch_hook's loop actually runs.
    bundle = tmp_path / "dps_bridge"
    bundle.mkdir()
    (bundle / "farever_dps.exe").write_bytes(b"MZ")
    (bundle / "farever_uninject.exe").write_bytes(b"MZ")
    monkeypatch.setattr(ga, "_hook_exe_candidates", lambda: [bundle])
    monkeypatch.setattr("sys.frozen", True, raising=False)
    cands = ga._hook_launch_candidates()
    assert cands[0] == bundle / "farever_dps.exe"
    assert not [c for c in cands if "uninject" in c.name.lower()]


def test_the_uninjector_copy_is_neither_shipped_nor_kept():
    """The other half of the invariant above.

    If nothing may launch the uninjector, nothing should package it either. A
    shipped-but-unused copy is not harmless: it is the exact file the old
    launch list reached for, and while `injector.c` still read its OWN filename
    the copy really was a second program. That dispatch is gone - the mode
    needs an explicit `--uninject` - but a published name nothing builds for is
    still a name the launch list can be handed. Removal is the crash-safe
    Python path (`core.proc.safe_uninject_hook`), and hot-uninject runs the
    injector with `--uninject`, so there is nothing left for a second copy to
    do.
    """
    root = Path(__file__).resolve().parent.parent
    spec = source_guard.read_text(root / "FareverPal.spec", encoding="utf-8")
    # It may - and should - be NAMED in the spec to explain the decision, but
    # no line of code may reference it: a commented rationale is not a data
    # entry, which is why this cannot be a plain substring check.
    mentions = [ln.strip() for ln in spec.splitlines()
                if "farever_uninject" in ln]
    assert mentions, "the spec no longer explains why the copy is gone"
    assert all(ln.startswith("#") for ln in mentions), (
        f"farever_uninject is named outside a comment: {mentions}")
    assert '"farever_dps.exe"' in spec          # the injector itself still ships
    # ...and it is not kept in the published tree either
    assert not (root / "dps_bridge" / "farever_uninject.exe").exists()


_INJECTOR_C = (Path(__file__).resolve().parent.parent.parent / "GameFiles"
               / "Farever" / "hooks" / "hook" / "farever_dps" / "injector.c")


def _c_code(text):
    """Source with its comments removed.

    A comment is where a retired mechanism gets explained, and explaining it
    means naming it - so the assertions below read the code, not the rationale.
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def test_the_injector_selects_its_mode_only_from_an_argument():
    """The filename dispatch, pinned gone.

    `injector.c` used to call GetModuleFileNameA and treat any path containing
    "uninject" as "uninject" - so a copy's NAME chose the mode and the same
    bytes were two different programs. A mode now needs an explicit --uninject
    argument, which is what takes the danger out of a stray copy: the worst it
    can do is inject, the one thing the binary is for.
    """
    if not _INJECTOR_C.is_file():
        pytest.skip("sibling GameFiles native tree is not checked out")
    code = _c_code(source_guard.read_text(_INJECTOR_C, encoding="utf-8", errors="replace"))

    assert "strstr" not in code, "injector.c is reading its own path again"
    assert "GetModuleFileNameA" not in code

    # The one assignment lives in the argv loop, after the flag comparisons.
    assert code.count("uninject_mode = true") == 1
    assert code.index("uninject_mode = true") > code.index("for (int i = 1; i < argc")
    assert '\"--uninject\"' in code
    assert "uninject_mode = false" in code        # injecting is the default

    # ...and it still finds the DLL beside itself, by the W form of the call.
    assert "GetModuleFileNameW" in code


def test_the_shipped_injector_carries_no_filename_dispatch():
    """The BINARY half of the assertion above.

    That test reads injector.c, which makes it blind to the failure mode that
    matters here: dps_bridge/farever_dps.exe is a build artifact, so fixing the
    source and shipping the previous binary leaves the dispatch live in every
    user's hands while every source-level test stays green. This is what
    caught that exact state on 2026-09-29 - the exe was still the pre-fix
    build, 381 bytes larger than what the fixed source compiles to.

    Two signals, both measured against a fresh build of the current source:

    * ``GetModuleFileNameA`` is absent. Reading its OWN path is what the
      dispatch did, and the W form is still imported (it resolves
      farever_dps.dll beside the exe), so its presence is also what proves this
      binary is the injector rather than something else wearing the name.
    * No standalone ``"uninject"`` literal. The rebuild still contains
      ``--uninject`` and ``/uninject`` - those are the explicit flags, and they
      are asserted present - so this is not simply "the word is gone": what
      cannot come back is a bare literal to compare its own path against.
    """
    path = _BRIDGE_DIR / "farever_dps.exe"
    if not path.is_file():
        pytest.skip("dps_bridge/farever_dps.exe not present")
    blob = source_guard.read_bytes(path)

    assert b"GetModuleFileNameW" in blob, "this is not the injector binary"
    assert b"--uninject" in blob, "the explicit flag must survive a rebuild"
    assert b"GetModuleFileNameA" not in blob, (
        "farever_dps.exe still reads its own path (GetModuleFileNameA), so it is "
        "a STALE BUILD of a fixed source - rebuild it:\n"
        "  GameFiles/Farever/hooks/build_all.bat")
    assert b"\x00uninject\x00" not in blob, (
        "farever_dps.exe still embeds a bare 'uninject' literal - the exact "
        "string the old filename dispatch matched its own path against. "
        "STALE BUILD: rebuild it.")


# ---------------------------------------------------------------------------
# the injector's verdict, not its exit code, decides success
# ---------------------------------------------------------------------------
# injector.c returns 0 on its uninject path and on "already resident", so an
# exit-code check alone calls a refusal a success - which is how a whole
# session sat at need_inject with zero events while the log looked healthy.
UNINJECTOR_OUTPUT = (
    "=======================================================\n"
    " FareverPal Combat Hook: Hot Uninjector\n"
    "=======================================================\n"
    "[INFO] farever_dps.dll is NOT currently loaded in PID 17092.\n"
    "Nothing to uninject."
)
OK_OUTPUT = "Successfully injected farever_dps.dll into PID 17092 (module: 0x1a2b3c)"


@pytest.mark.parametrize("rc,out", [
    (0, UNINJECTOR_OUTPUT),                      # the bug this was added for
    (0, "[INFO] farever_dps.dll already resident at 0x7ff in PID 5.\n"
        "[ERROR] Safe unload failed (rc=1). NOT injecting over a\n"
        "live instance - restart the game, then re-run the injector."),
    (0, "Error: farever_dps.dll not found at D:\\x\\farever_dps.dll"),
    (0, "Error: Could not open process 17092 (Error 5)"),
    (8, "Error: LoadLibraryW failed in target process (code 0)."),
    (2, ""),                                     # non-zero, silent
    (3, "unrelated chatter"),                    # non-zero, no marker
])
def test_inject_output_classified_as_failure(rc, out):
    pytest.importorskip("PySide6")
    from farever_companion.ui.game_attach import _classify_inject_output

    ok, reason = _classify_inject_output(rc, out)
    assert ok is False
    assert reason, "a failure must carry a reason the UI can show"


@pytest.mark.parametrize("rc,out", [
    (0, OK_OUTPUT),
    (None, OK_OUTPUT),                            # status not captured
    (0, ""),                                      # silent but clean
    (0, "[OK] Previous instance safely unloaded. Loading fresh copy..."),
])
def test_inject_output_classified_as_success(rc, out):
    pytest.importorskip("PySide6")
    from farever_companion.ui.game_attach import _classify_inject_output

    assert _classify_inject_output(rc, out)[0] is True


def test_a_refused_inject_reports_inject_failed_not_need_inject():
    """The whole point: a refusal must be VISIBLE on the card.

    need_inject means "nobody has injected yet", which is exactly what the
    user is told to fix; inject_failed means "we tried and it refused", which
    is a different problem with a different fix. They must not collapse into
    one indistinguishable state.
    """
    inject, _ = _bridge(_BridgeProc(), mode="injector")
    assert inject.status() == "need_inject"

    inject.inject_problem = "farever_dps.dll is NOT currently loaded"
    assert inject.status() == "inject_failed"
    assert _bridge(_BridgeProc(), mode="proxy")[0].status() == "missing_proxy"


def test_inject_problem_clears_once_the_dll_is_actually_loaded():
    """A stale failure reason must not outlive the state it explained."""
    inject, _ = _bridge(_BridgeProc(option=2), mode="injector")
    inject.inject_problem = "injector exited 8"
    assert inject.status() == "bridge_ready"     # loaded wins
    assert inject.inject_problem == ""           # and the reason is dropped


def test_note_inject_result_drives_the_manager_status():
    """DamageSourceManager.note_inject_result is the wire from the launcher."""
    mgr = DamageSourceManager(dps_fakes_proc())
    mgr.set_mode("injector")
    mgr.ensure_started()
    assert mgr.status() == "need_inject"

    mgr.note_inject_result(False, "LoadLibraryW failed in target process")
    assert mgr.inject_problem == "LoadLibraryW failed in target process"
    assert mgr.status() == "inject_failed"

    mgr.note_inject_result(True)
    assert mgr.inject_problem == ""
    assert mgr.status() == "need_inject"         # not loaded yet, but not failed

    # A memory-mode manager owns no bridge: the verdict is dropped, not stored.
    mgr.set_mode("memory")
    mgr.ensure_started()
    mgr.note_inject_result(False, "nope")
    assert mgr.inject_problem == ""


def test_inject_failed_reaches_every_dps_surface():
    """One vocabulary, four surfaces: no surface may fall through to a
    generic 'off'/empty string and hide the failure."""
    from farever_companion.ui import theme
    from farever_companion.ui.dps_source_text import (
        capture_badge, empty_hint, source_status)

    mgr = DamageSourceManager(dps_fakes_proc())
    mgr.set_mode("injector")
    mgr.ensure_started()
    mgr.note_inject_result(False, "LoadLibraryW failed in target process")

    text, color = source_status(mgr)
    assert "INJECT FAILED" in text and "LoadLibraryW" in text
    assert color == theme.DANGER

    hint = empty_hint(mgr, "default")
    assert "FAILED" in hint and "LoadLibraryW" in hint and hint != "default"

    label, badge_color, tip = capture_badge(mgr)
    assert label == "⚠" and badge_color == theme.DANGER
    assert "LoadLibraryW" in tip

    # need_inject keeps its softer "not attached" wording - the two states
    # must not render identically.
    mgr.note_inject_result(True)
    assert source_status(mgr)[0] == "SOURCE: ⚠ Option 2 — injector not attached"
    assert capture_badge(mgr)[1] == theme.ORANGE


def test_failed_launch_allows_a_retry(monkeypatch):
    """A refusal must not burn the one-shot per-PID guard: the cause is
    usually fixable (game folder DLL, permissions) and re-attaching is the
    documented recovery."""
    pytest.importorskip("PySide6")
    import farever_companion.ui.game_attach as ga

    # An empty candidate list: the "injector not found" path, which must also
    # release the guard (without this the repo's own dps_bridge/ would be
    # found and a REAL injector would run against PID 4242).
    monkeypatch.setattr(ga, "_hook_launch_candidates", list)
    seen: list = []
    ga._INJECTED_PIDS.clear()
    try:
        t = ga._try_launch_hook(4242, log_cb=None,
                                on_result=lambda ok, why: seen.append((ok, why)))
        t.join(timeout=5)
        assert seen == [(False, "farever_dps.exe injector not found")]
        assert 4242 not in ga._INJECTED_PIDS
    finally:
        ga._INJECTED_PIDS.clear()


def test_launch_hook_reports_the_injectors_verdict(monkeypatch, tmp_path):
    """The worker must hand its classified verdict to on_result, and a
    refusal must say so in the log instead of only echoing the output."""
    pytest.importorskip("PySide6")
    import subprocess
    import farever_companion.ui.game_attach as ga

    exe = tmp_path / "farever_dps.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(ga, "_hook_launch_candidates", lambda: [exe])

    seen: list = []
    logs: list = []

    class _Proc:
        returncode = 0
        def communicate(self, timeout=None):
            return UNINJECTOR_OUTPUT, ""
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _Proc())

    ga._INJECTED_PIDS.clear()
    try:
        t = ga._try_launch_hook(777, force=True, log_cb=logs.append,
                                on_result=lambda ok, why: seen.append((ok, why)))
        t.join(timeout=5)
    finally:
        ga._INJECTED_PIDS.clear()

    assert seen and seen[0][0] is False, "the refusal must reach on_result"
    assert any("INJECT FAILED" in line for line in logs), logs
    # ...and the PID is free again so re-attaching can retry.
    assert 777 not in ga._INJECTED_PIDS


# ---------------------------------------------------------------------------
# the inject watchdog: retry a deaf capture, but never past the located gate
# ---------------------------------------------------------------------------
def _retry_kw(**over):
    """Baseline inputs for a retry that SHOULD fire; override per case."""
    kw = dict(attached=True, located=True, mode="injector", hook_enabled=True,
              capture_status="need_inject", in_fight=True,
              attempts=0, last_attempt_ts=0.0, now=1000.0)
    kw.update(over)
    return kw


def test_watchdog_retries_when_a_boss_fight_gets_no_events():
    """The whole feature: boss engaged, capture deaf -> re-run the injector."""
    from farever_companion.ui.inject_retry import retry_decision

    retry, why = retry_decision(**_retry_kw())
    assert retry is True
    assert "boss fight" in why and "0 events" in why

    # A recorded refusal (inject_failed) is the same anomaly, same answer.
    assert retry_decision(**_retry_kw(capture_status="inject_failed"))[0] is True


def test_watchdog_never_injects_before_the_player_is_located():
    """The gate the user has to see.

    The located transition is the ONLY thing that makes injection safe (the
    world and its runtime table must be up before the DLL is loaded). A
    retry must not become a way around it - no matter how sick the capture
    looks or how many attempts are already spent.
    """
    from farever_companion.ui.inject_retry import retry_decision

    retry, why = retry_decision(**_retry_kw(located=False))
    assert retry is False
    assert "not located" in why


@pytest.mark.parametrize("over,expect", [
    (dict(attached=False), "not attached"),
    (dict(mode="proxy"), "not injector"),
    (dict(mode="memory"), "not injector"),
    (dict(hook_enabled=False), "disabled"),
    (dict(in_fight=False), "no boss"),
    (dict(capture_status="live"), "live"),
    (dict(capture_status="bridge_ready"), "bridge_ready"),
    (dict(capture_status="off"), "off"),
])
def test_watchdog_stays_quiet_in_every_other_state(over, expect):
    """One anomaly, many quiet paths - a timer that fires on any of these
    would re-inject the DLL all evening during ordinary trash fights."""
    from farever_companion.ui.inject_retry import retry_decision

    retry, why = retry_decision(**_retry_kw(**over))
    assert retry is False
    assert expect in why


def test_watchdog_budget_is_bounded_and_rate_limited():
    """Bounded twice over: a hard attempt cap, and a floor between runs."""
    from farever_companion.ui.inject_retry import RETRY_MAX, retry_decision

    assert retry_decision(**_retry_kw(attempts=RETRY_MAX))[0] is False
    assert "budget" in retry_decision(**_retry_kw(attempts=RETRY_MAX))[1]
    # Below the cap but inside the interval floor -> still quiet.
    assert retry_decision(**_retry_kw(attempts=1, last_attempt_ts=990.0))[0] is False
    # Past the floor -> fires again.
    assert retry_decision(**_retry_kw(attempts=1, last_attempt_ts=900.0))[0] is True


def test_watchdog_budget_is_refunded_once_the_capture_recovers():
    """A bad opening must not leave the watchdog spent for the whole session:
    a genuine later regression deserves a full allowance."""
    from farever_companion.ui.inject_retry import RETRY_MAX, InjectRetryState

    st = InjectRetryState()
    st.attempts = RETRY_MAX
    st.observe(capture_status="live")
    assert st.attempts == 0

    st.attempts = RETRY_MAX
    st.observe(capture_status="need_inject")   # still deaf: no refund
    assert st.attempts == RETRY_MAX

    st.reset()
    assert st.attempts == 0 and st.last_attempt_ts == 0.0


def test_watchdog_state_delegates_its_own_counters():
    from farever_companion.ui.inject_retry import InjectRetryState

    st = InjectRetryState()
    st.note_attempt(now=500.0)
    assert st.attempts == 1 and st.last_attempt_ts == 500.0
    base = {k: v for k, v in _retry_kw().items()
            if k not in ("attempts", "last_attempt_ts", "now")}
    # 5 s after the attempt -> inside the 20 s floor, so quiet.
    assert st.decide(**base, now=505.0)[0] is False
    # Long after it, and budget remains -> fires, and says which attempt it is.
    retry, why = st.decide(**base, now=900.0)
    assert retry is True and "2/3" in why


def test_watchdog_tick_never_fires_without_a_live_model():
    """The tick runs on a plain QTimer, so it also fires while detach is
    tearing the model down. It must be a silent no-op, not an exception."""
    pytest.importorskip("PySide6")
    import farever_companion.ui.game_attach as ga

    ctl = ga.GameAttachmentController.__new__(ga.GameAttachmentController)
    ctl._stopped = False
    ctl.proc = None
    ctl.model = None
    ctl._inject_retry = ga.InjectRetryState()
    ctl._tick_inject_retry()          # must not raise

    ctl._stopped = True
    ctl.proc = object()
    ctl.model = object()
    ctl._tick_inject_retry()          # stopped -> still must not raise


def test_watchdog_is_wired_into_the_controller_lifecycle():
    """The timer must start, stop, and share the budget reset with detach -
    a watchdog left running after detach would inject into a dead PID."""
    pytest.importorskip("PySide6")
    from pathlib import Path
    import farever_companion.ui.game_attach as ga

    src = source_guard.read_text(ga.__file__, encoding="utf-8")
    assert "self._inject_timer.start()" in src
    assert "self._inject_timer.stop()" in src
    assert "self._inject_retry.reset()" in src      # in detach()
    assert "self._inject_retry.note_attempt()" in src
    assert "_inject_timer.timeout.connect(self._tick_inject_retry)" in src
    # The tick itself lives in the watchdog mixin (the controller owns only the
    # timer and the launcher), so the retry's two load-bearing lines are pinned
    # there: it must go through the one launcher, with force so the per-PID
    # one-shot guard (already consumed by the first attempt) cannot block it,
    # and it must still pass the player-located gate.
    import farever_companion.ui.inject_watchdog as wd

    tick = source_guard.read_text(wd.__file__, encoding="utf-8")
    assert "self._launch_injector(force=True)" in tick
    assert "located=bool(self._located_shown)" in tick


def test_zone_rehunt_request_reaches_the_reader_one_loop_pass_later(monkeypatch):
    """force_wide_rehunt() must survive the thread boundary: MemorySource arms
    the reader's one-shot wide sweep within one loop pass, and the request
    does not linger (one-shot, like the tracker's zone-transition arm)."""
    proc = dps_fakes_proc()
    src = dps_memory.MemorySource(
        proc, emit=lambda evs: None, log_line=None,
        type_hint=None, result_type_hint=None,
        active=lambda: True,
    )
    dr = src.ensure_reader()

    assert dr._force_wide_until == 0.0
    src.force_wide_rehunt("zone enter")
    assert src._force_wide == "zone enter"     # parked until the loop pass
    src._apply_forced_rehunt(dr)
    assert src._force_wide == ""               # consumed
    assert dr._force_wide_until > 0.0          # reader armed for the next hunt


def dps_fakes_proc():
    from tests.fakemem import FakeProc, HeapBuilder
    proc = FakeProc()
    hb = HeapBuilder(proc)
    hb.make_type("ui.comp.DamageDisplay", fields={"dmg": 0x4e0})
    hb.make_type("st.skill.DamageResult", fields={"amount": 0x58})
    return proc


# --- forced re-hunt blind-window observability (2026-09-19) ----------------
def test_forced_rehunt_latency_logged_on_wide_sweep():
    """The pending request's age at the wide sweep is the user's blind window:
    `pool re-anchored in Xs (why)` lands once in the Activity Log when the
    reader's `_last_full_derive` advances (the sweep actually ran)."""
    reader = _Reader()
    src, _ = _source(reader=reader)
    lines: list[str] = []
    src.log_line = lines.append

    src.force_wide_rehunt("zone enter")
    src._rehunt_requested_at -= 7.0   # pretend the request sat 7s before the sweep
    assert src._rehunt_requested_at > 0.0

    # Sweep completes inside refresh_ranges (production: _hunt_instances bumps
    # _last_full_derive mid-derive) — simulate that advance on the fake.
    def _sweep_runs():
        reader.calls["refresh_ranges"] += 1
        reader._ranges_ready = True
        reader._last_full_derive = time.monotonic()
        reader._last_sweep_hits = 2          # the sweep found the moved pool
    reader.refresh_ranges = _sweep_runs
    src._refresh_timed(reader)

    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("pool re-anchored in ")
    assert "7.0s" in line and "zone enter" in line
    assert src._rehunt_requested_at == 0.0   # one-shot: cleared after logging


def test_unforced_refresh_does_not_log_or_clear():
    """A maintenance derive without a pending request logs nothing — the
    pending state must survive for the sweep that actually satisfies it."""
    reader = _Reader()
    src, _ = _source(reader=reader)
    lines: list[str] = []
    src.log_line = lines.append

    src.force_wide_rehunt("zone enter")
    src._rehunt_requested_at -= 3.0

    reader._ranges_ready = True
    src._refresh_timed(reader)                    # derive does NOT advance
    assert lines == []
    assert src._rehunt_requested_at > 0.0         # pending until the sweep

    def _sweep_runs():                            # now the wide sweep runs
        reader.calls["refresh_ranges"] += 1
        reader._ranges_ready = True
        reader._last_full_derive = time.monotonic()
        reader._last_sweep_hits = 3               # and FOUND displays
    reader.refresh_ranges = _sweep_runs
    src._refresh_timed(reader)
    assert len(lines) == 1 and "zone enter" in lines[0]


def test_second_rehunt_request_keeps_earliest_timestamp():
    """Zone-out right after zone-in: the second request must not reset the
    clock — the logged latency covers the whole window from the FIRST ask."""
    reader = _Reader()
    src, _ = _source(reader=reader)
    src.force_wide_rehunt("zone enter")
    src._rehunt_requested_at -= 10.0
    src.force_wide_rehunt("zone exit")
    assert src._rehunt_why == "zone exit"         # latest reason shown
    assert src._rehunt_requested_at > 0.0
    src._rehunt_requested_at -= 10.0              # (guard) if it HAD reset, this would re-age it
    assert src._rehunt_requested_at > 0.0
    assert src._rehunt_requested_at < time.monotonic() - 18.0  # still the first ask


# --- capture badge (Top DPS title bar) -------------------------------------
def test_capture_badge_maps_the_status_vocabulary(make):
    """The badge must speak the same status() vocabulary as source_status,
    with three visual buckets: LIVE / CAL / IDLE / warn."""
    from PySide6 import QtCore  # noqa: F401  (Qt must be up for ui imports)
    from farever_companion.ui.dps_source_text import capture_badge
    from farever_companion.ui import theme

    from types import SimpleNamespace
    mgr = make()
    mgr.ensure_started()   # status() is "off" until the engines exist

    mgr.set_mode("injector")
    assert capture_badge(mgr) == ("⚠", theme.ORANGE,
                                  "Option 2 — injector not attached")

    # Drive the REAL MemorySource states (status() is computed, not stored):
    # no reader yet -> locating; type found, ranges unmapped -> mapping;
    # calibrated and quiet -> silent; fresh event -> live; no scanner -> warn.
    mgr.set_mode("memory")
    text, color, tip = capture_badge(mgr)
    assert text == "CAL" and color == theme.GOLD and "locating" in tip

    mgr.memory.reader = SimpleNamespace(_type_ptr=0x1000, _ranges_ready=False)
    assert capture_badge(mgr)[0] == "CAL"

    mgr.memory.reader._ranges_ready = True
    text, color, _tip = capture_badge(mgr)
    assert text == "IDLE" and color == theme.DIM

    mgr.memory._last_event_ts = time.monotonic()
    text, color, _tip = capture_badge(mgr)
    assert text == "LIVE" and color == theme.GOOD

    mgr.memory.proc.has_scan = False
    text, color, _tip = capture_badge(mgr)
    assert text == "⚠" and color == theme.DANGER
    mgr.shutdown()


def test_capture_badge_live_names_the_engine_and_count():
    from farever_companion.ui.dps_source_text import capture_badge
    from farever_companion.ui import theme
    from tests.dps_fakes import _FakeSource

    src = _FakeSource()          # _status = "live", counts() -> (1, 0)
    text, color, tip = capture_badge(src)
    assert text == "LIVE" and color == theme.GOOD
    # The fake has no live_source_name(); the "damage numbers" fallback is
    # the designed behavior (same defensive pattern as source_status).
    assert "damage numbers" in tip and "1 events" in tip

    src.damage_stale = True      # silent + stale is a warning, not idle
    src._status = "silent"
    text, color, tip = capture_badge(src)
    assert text == "⚠" and color == theme.ORANGE and "stale" in tip.lower()


def test_capture_badge_empty_when_nothing_to_say():
    from farever_companion.ui.dps_source_text import capture_badge
    from tests.dps_fakes import _FakeSource

    assert capture_badge(None) == ("", theme_muted(), "")
    src = _FakeSource()
    src._status = "off"
    assert capture_badge(src)[0] == ""


def theme_muted():
    from farever_companion.ui import theme
    return theme.MUTED


# --- armed re-hunts fire immediately, not on the derive scheduler (2026-09-19) ---

def test_armed_rehunt_sweeps_in_same_pass_when_ranges_ready():
    """The zone-in arm must produce an actual sweep THIS loop pass.

    The reader's arm only bypasses the sweep's internal rate gate; the
    _calibrate scheduler can sit up to 300 s away on the healthy cadence
    (ranges 'ready' but stale after a zone change, combat hint off). Live:
    outgoing damage lagged 10 s+ into a pull while taken estimates showed.
    """
    src, events = _source(reader=_Reader())
    dr = src.reader
    dr.find_type(); dr.find_result_type()         # a WORKING reader re-baselines
    src.force_wide_rehunt("zone enter")           # request...
    src._apply_forced_rehunt(dr)                  # ...applied this pass (one-shot)
    assert dr._force_wide_until > time.monotonic()
    src._rehunt_now_if_armed(dr)                  # the new immediate-fire step
    assert dr.calls["refresh_ranges"] == 1        # swept NOW, not on a cadence


def test_armed_rehunt_sweeps_when_types_located_but_ranges_missing():
    """Cold reader (located, no ranges yet): sweep instead of waiting for a
    locate/map cadence — the old path left a multi-second cold gap."""
    src, events = _source(reader=_Reader())
    dr = src.reader
    dr.find_type(); dr.find_result_type()         # located, ranges NOT ready
    assert dr._ranges_ready is False
    src.force_wide_rehunt("zone enter")
    src._apply_forced_rehunt(dr)
    src._rehunt_now_if_armed(dr)
    assert dr.calls["refresh_ranges"] == 1


def test_unarmed_reader_is_never_swept_by_the_immediate_step():
    """No arm -> the immediate step is a no-op; ordinary cadences stay in charge."""
    src, events = _source(reader=_Reader())
    dr = src.reader
    dr.find_type(); dr.find_result_type()
    src._rehunt_now_if_armed(dr)
    assert dr.calls["refresh_ranges"] == 0


def test_armed_rehunt_does_not_double_sweep_once_consumed():
    """The reader's arm is one-shot: after the forced sweep consumes it, the
    immediate step cannot re-sweep every pass (loop-hog)."""
    src, events = _source(reader=_Reader())
    dr = src.reader
    dr.find_type(); dr.find_result_type()
    src.force_wide_rehunt("zone enter")
    src._apply_forced_rehunt(dr)
    src._rehunt_now_if_armed(dr)
    assert dr.calls["refresh_ranges"] == 1
    assert dr._force_wide_until == 0.0            # arm consumed by the sweep
    src._rehunt_now_if_armed(dr)                  # next pass
    assert dr.calls["refresh_ranges"] == 1        # still one sweep


def test_locating_reader_is_not_swept_by_the_immediate_step():
    """Pre-locate reader: the immediate step leaves type-location to the
    locate cadence (a sweep before any types exist is wasted work)."""
    src, events = _source(reader=_Reader())
    dr = src.reader
    assert dr._type_ptr is None
    src._apply_forced_rehunt(dr)
    src._rehunt_now_if_armed(dr)
    assert dr.calls["refresh_ranges"] == 0
    assert dr.calls["find_type"] == 0             # did not force a locate either


# --- blind rescue: the poller's retry-aware due check (2026-09-19) ---------
def test_an_unresolved_rescue_retries_when_due_not_every_pass():
    """A rescue whose sweep found nothing stays armed and retries on its
    backed-off cadence. The poller must not re-sweep every pass (loop-hog),
    but once the retry falls due it MUST sweep — waiting out the 120 s
    maintenance gate is what left a whole dungeon pull undecoded."""
    src, events = _source(reader=_Reader())
    dr = src.reader
    dr.find_type(); dr.find_result_type()
    now = time.monotonic()
    dr._force_wide_until = now + 60.0         # re-armed after an empty sweep
    dr._force_retry_after = now + 60.0        # ...and backing off
    src._rehunt_now_if_armed(dr)
    assert dr.calls["refresh_ranges"] == 0    # not due yet: no sweep

    dr._force_retry_after = time.monotonic()  # the retry is due now
    assert dr.rescue_sweep_due() is True
    src._rehunt_now_if_armed(dr)
    assert dr.calls["refresh_ranges"] == 1


def test_an_unresolved_rescue_is_logged_as_failed_not_silently_dropped():
    """The blind window must be visible: a rescue that never anchored logs a
    FAILED line carrying the whole latency, instead of vanishing — that
    silence is what made the 2026-09-19 dungeon pull unreadable."""
    reader = _Reader()
    src, _ = _source(reader=reader)
    lines: list[str] = []
    src.log_line = lines.append

    src.force_wide_rehunt("zone enter")
    src._rehunt_requested_at -= 90.0          # the retry window closed long ago
    reader._force_wide_until = 0.0            # nothing is armed any more
    reader._last_sweep_hits = 0               # ...and no sweep found the pool

    src._refresh_timed(reader)
    assert len(lines) == 1
    assert "FAILED" in lines[0] and "zone enter" in lines[0]
    assert "90.0s" in lines[0]
    assert src._rehunt_requested_at == 0.0    # reported once, not per pass


def test_the_live_stream_rollup_is_paced_by_time_not_by_event_count():
    """The "Live stream healthy" line is a liveness note, so it is paced by
    TIME. It used to print every 50 events, which meant the harder the player
    fought the faster it spoke - a hard-mode trash pull (~50 events every one
    to two seconds) buried the per-hit lines and any attribution warning the
    Activity Log exists to surface. One minute apart is enough to say "still
    alive" without drowning the log.
    """
    from farever_companion.core.damage_events import DamageEvent

    src, _ = _bridge()
    logs: list[str] = []
    src.log_line = logs.append
    src.heartbeat_gate = lambda: True      # an instance run narrates

    def _batch(n: int) -> None:
        src._accept([DamageEvent(amount=10.0, skill="A", source_addr=1,
                                 target_addr=2) for _ in range(n)])

    # First ten hits print individually (the confirmation) and arm the clock.
    _batch(10)
    assert not any("Live stream healthy" in line for line in logs), \
        "the first-hits lines already confirm the stream"
    t0 = src._last_health_line_ts
    assert t0 > 0.0

    # A hundred more events inside the period must NOT speak.
    _batch(100)
    assert not any("Live stream healthy" in line for line in logs), \
        "event count alone drove the old line; it must be time-paced"

    # Once a full period has elapsed, exactly one line - not one per 50 events.
    src._last_health_line_ts -= HEALTH_LINE_PERIOD_S + 1.0
    _batch(100)
    said = [line for line in logs if "Live stream healthy" in line]
    assert len(said) == 1, said
    assert f"{src.events_n} combat events captured" in said[0]
