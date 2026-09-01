"""Headless tests for DamageSourceManager (core/dps_source.py).

Covers the consumer cursor semantics of the shared event ring and the status
states; the live locate/poll machinery is exercised against fake memory in
test_damage.py and needs no thread here.
"""
from __future__ import annotations

import time

from farever_companion.core.damage_events import DamageEvent
from farever_companion.core.dps_source import DamageSourceManager


class _NoScan:
    has_scan = False


class _Scan:
    has_scan = True


def _ev(amount: float, skill: str = "Sword_Base_Attack") -> DamageEvent:
    return DamageEvent(amount=amount, skill=skill)


def test_noscan_status():
    mgr = DamageSourceManager(_NoScan())
    assert mgr.status() == "noscan"
    mgr.shutdown()


def test_ring_cursor_semantics():
    mgr = DamageSourceManager(_Scan())   # thread not started in tests
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


def test_status_off_until_started():
    mgr = DamageSourceManager(_Scan())
    assert mgr.status() == "off"        # thread not running (no has_scan gate ok)
    mgr.shutdown()


def test_type_hint_plumbing():
    """The cached type pointer seeds the reader to skip the locate scan."""
    mgr = DamageSourceManager(_Scan(), type_hint="0x1234")
    assert mgr._ensure_reader()._type_hint == "0x1234"

    # set_type_hint before start also works
    mgr2 = DamageSourceManager(_Scan())
    mgr2.set_type_hint("0x9999")
    assert mgr2._ensure_reader()._type_hint == "0x9999"
    mgr.shutdown(); mgr2.shutdown()


def test_locate_progress_ticks_during_scan():
    """The status line shows locate attempts/elapsed while scanning."""
    mgr = DamageSourceManager(_Scan())
    assert mgr.locate_progress() == ""          # nothing locating yet

    import time as _t
    mgr._locate_running = True
    mgr._locate_started = _t.monotonic() - 33.0
    mgr._locate_attempts = 1                    # scan 2 in progress
    prog = mgr.locate_progress()
    assert "2" in prog and "33" in prog        # e.g. "scan 2 · 33s"
    mgr._locate_running = False
    assert mgr.locate_progress() == ""
    mgr.shutdown()


def test_located_pointers_read_reader_cache():
    """located_pointers exposes calibrated pointers for persistence."""
    mgr = DamageSourceManager(_Scan())
    assert mgr.located_pointers() == (None, None)   # no reader yet

    class _R:
        type_ptr = 0x7ffc1234
        _result_type_ptr = 0x7ffc5678

    mgr._reader = _R()
    assert mgr.located_pointers() == (0x7ffc1234, 0x7ffc5678)
    mgr.shutdown()


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


def test_damage_stale_follows_reader_diag():
    """damage_stale tracks found-but-undecoded reader diagnostics."""
    mgr = DamageSourceManager(_Scan())
    assert mgr.damage_stale is False        # no reader, no diag
    assert mgr.diagnostics() == {}

    # simulate the reader's per-poll diagnostics directly
    mgr._reader = type("_R", (), {"_diag": {"displays": 0, "ok": 0}})()
    assert mgr.damage_stale is False        # nothing found yet - just quiet

    mgr._reader = type("_R", (), {"_diag": {"displays": 12, "ok": 0,
                                               "no_res": 11, "bad_amount": 1}})()
    assert mgr.damage_stale is True         # found displays, decoded nothing
    assert mgr.diagnostics()["displays"] == 12


def test_has_decoded_tracks_first_decoded_event():
    """has_decoded flips True only on the first decoded event."""
    mgr = DamageSourceManager(_Scan())
    assert mgr.has_decoded is False
    mgr._first_event_s = 4.7
    assert mgr.has_decoded is True
    mgr.shutdown()
    mgr.shutdown()


def test_timing_text_stages():
    """The status line's stage timings only list completed stages, in order."""
    mgr = DamageSourceManager(_Scan())
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


def test_source_status_suffix_renders_timing():
    """source_status appends stage timings for the UI line."""
    from farever_companion.ui.dps_source_text import _timing_suffix
    mgr = DamageSourceManager(_Scan())
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


def test_hook_event_parsing_damage():
    """Verify parsing of hooked damage events."""
    mgr = DamageSourceManager(_Scan())
    json_hit = '{"t":"hit","amt":1250.5,"crit":1,"kill":0,"sk":"Thunderclap","src":"HeroPlayer","tgt":"BossMob","me":1}'
    ev = mgr._parse_hook_event(json_hit)
    assert ev is not None
    assert ev.amount == 1250.5
    assert ev.skill == "Thunderclap"
    assert ev.is_crit is True
    assert ev.is_kill is False
    assert ev.source_name == "HeroPlayer"
    assert ev.target_name == "BossMob"
    assert ev.is_me is True
    assert ev.is_heal is False
    mgr.shutdown()


def test_hook_event_parsing_heal():
    """Verify parsing of hooked heal events."""
    mgr = DamageSourceManager(_Scan())
    json_heal = '{"t":"heal","amt":450.0,"landed":400.0,"sk":"PrayerOfHealing","src":"Priest","tgt":"Warrior","me":0}'
    ev = mgr._parse_hook_event(json_heal)
    assert ev is not None
    assert ev.amount == 450.0
    assert ev.skill == "PrayerOfHealing"
    assert ev.source_name == "Priest"
    assert ev.target_name == "Warrior"
    assert ev.is_me is False
    assert ev.is_heal is True
    mgr.shutdown()


def test_hook_event_parsing_kill():
    """Verify parsing of hooked kill events."""
    mgr = DamageSourceManager(_Scan())
    json_kill = '{"t":"kill","src":"Mage","tgt":"Goblin","me":1}'
    ev = mgr._parse_hook_event(json_kill)
    assert ev is not None
    assert ev.is_kill is True
    assert ev.source_name == "Mage"
    assert ev.target_name == "Goblin"
    assert ev.is_me is True
    mgr.shutdown()


def test_hook_event_malformed_resilience():
    """Verify malformed JSON or unknown event types are gracefully ignored."""
    mgr = DamageSourceManager(_Scan())
    assert mgr._parse_hook_event("") is None
    assert mgr._parse_hook_event("not json") is None
    assert mgr._parse_hook_event('{"t":"unknown_type"}') is None
    mgr.shutdown()


def test_hook_live_source_status():
    """When hook events are received, live_source_name reflects combat bridge."""
    mgr = DamageSourceManager(_Scan())
    assert mgr.live_source_name == "memory poller"
    mgr._hook_events_received = 42
    assert mgr.live_source_name == "combat bridge"
    assert "combat bridge (42 events)" in mgr.timing_text()
    mgr.shutdown()


# --- hook-install status telemetry ("why is the bridge silent?") ---------
def _status(stage: str, ok: int, msg: str) -> dict:
    return {"t": "status", "s": stage, "ok": ok, "m": msg}


def test_bridge_status_logged_and_healthy():
    """Every DLL status line lands in the Activity Log; a healthy run has no issue."""
    mgr = DamageSourceManager(_Scan())
    logs = []
    mgr.log_line = logs.append
    mgr._handle_bridge_status(_status("boot", 1, "bridge init start"))
    mgr._handle_bridge_status(_status("table", 1, "functions_ptrs table located"))
    mgr._handle_bridge_status(_status("hook", 1, "onInflictDamage findex 4865 -> OK"))
    mgr._handle_bridge_status(_status("mh_enable", 1,
                                      "hooks armed - combat events stream over UDP"))
    assert any("Combat Bridge: hooks armed" in line for line in logs)
    assert not any("FAILED" in line for line in logs)
    assert mgr.bridge_issue() == ""
    mgr.shutdown()


def test_bridge_issue_reports_table_miss():
    """A table-miss session is surfaced as the reason the bridge stays silent."""
    mgr = DamageSourceManager(_Scan())
    logs = []
    mgr.log_line = logs.append
    mgr._handle_bridge_status(_status("boot", 1, "bridge init start"))
    mgr._handle_bridge_status(_status("table", 0,
        "functions_ptrs table NOT found after 50 scans "
        "(fmt.hdll anchors 33131/33132/35144 unmatched)"))
    issue = mgr.bridge_issue()
    assert "NOT found" in issue
    assert any("FAILED" in line for line in logs)
    mgr.shutdown()


def test_bridge_issue_shows_hook_failures_before_arm():
    """Per-function MinHook failures show while the session has not armed."""
    mgr = DamageSourceManager(_Scan())
    mgr._handle_bridge_status(_status("boot", 1, "bridge init start"))
    mgr._handle_bridge_status(_status("hook", 0,
        "onInflictDamage findex 4865 -> MH_ERROR_NOT_EXECUTABLE"))
    issue = mgr.bridge_issue()
    assert "MH_ERROR_NOT_EXECUTABLE" in issue
    mgr.shutdown()


def test_bridge_issue_respects_session_boundary():
    """A failed OLD load never leaks into a healthy new one after a game restart."""
    mgr = DamageSourceManager(_Scan())
    mgr._handle_bridge_status(_status("boot", 1, "old boot"))
    mgr._handle_bridge_status(_status("table", 0, "old table miss"))
    # game restarted; the new DLL load hooked cleanly
    mgr._handle_bridge_status(_status("boot", 1, "new boot"))
    mgr._handle_bridge_status(_status("table", 1, "functions_ptrs table located"))
    mgr._handle_bridge_status(_status("mh_enable", 1,
                                      "hooks armed - combat events stream over UDP"))
    assert mgr.bridge_issue() == ""
    mgr.shutdown()


def test_bridge_status_line_never_becomes_event():
    """Status datagrams must not be parsed as combat events."""
    mgr = DamageSourceManager(_Scan())
    assert mgr._parse_hook_event(
        '{"t":"status","s":"table","ok":0,"m":"boom"}') is None
    mgr.shutdown()


def test_kill_uid_event_parses_as_pure_kill():
    """notifyUnitKilled__impl lines carry only the killed unit's id under uid."""
    mgr = DamageSourceManager(_Scan())
    ev = mgr._parse_hook_event('{"t":"kill","uid":"Boss_Foo"}')
    assert ev is not None
    assert ev.is_kill is True
    assert ev.amount == 0.0
    assert ev.target_name == "Boss_Foo"      # uid folded into the target
    assert ev.is_heal is False
    mgr.shutdown()

