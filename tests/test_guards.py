"""test_guards - consolidated subsystem tests.

Merged without changing any test, from:
  * tests/test_source_guard.py
  * tests/test_sound_guard.py
  * tests/test_crash_log.py
  * tests/test_session_audit.py
  * tests/test_config_flavor.py
  * tests/test_copy_utils.py
  * tests/test_master_backup.py
  * tests/test_updater.py

Imports are deduped and the shared `_qapp` fixture kept once;
where two source files bound one name differently, the later
was renamed `<name>__<file>` and only its own file's references
follow. See AGENTS.md: the suite is the spec.
"""
from __future__ import annotations


# --- shared imports ---
import os
import time
import pytest
from tests.source_guard import (ConcurrentRewrite, read_bytes, read_text,
                                reset_session, stamp)
import sys
import threading
import types
from farever_companion.runtime import sound
import subprocess
from datetime import datetime
from pathlib import Path
from farever_companion.runtime import crash_log
from PySide6 import QtCore  # noqa: E402
from farever_companion import app as appmod
from farever_companion.config import audit_settings_event
import json
from farever_companion import config
from PySide6 import QtTest, QtWidgets
from farever_companion.ui.copy_utils import copy_text, copy_with_feedback
from farever_companion.backup import master as mb
from farever_companion.runtime.persist import atomic_write_json
import zipfile
from farever_companion.core import updater
from farever_companion.core.updater import parse_version, is_newer, UpdateInfo
from tests.qt_helpers import destroy_panel  # noqa: E402
from farever_companion.config import Settings  # noqa: E402
from farever_companion.ui.control_panel import ControlPanel, NAV  # noqa: E402
from farever_companion.ui.updater_ui import update_pill_text  # noqa: E402

# ========================================================================
# from tests/test_source_guard.py
# ========================================================================

"""The concurrent-rewrite guard: it must be quiet unless something really moved.

A guard that cries wolf is worse than no guard - it teaches the reader to skip
the note and go straight to the assertion. So these tests pin both directions:
a stable file is read normally and recorded, and only a genuine move inside a
session raises. `tests/source_guard.py` has the reasoning; the case it exists
for is a concurrent writer replacing a file a source-contract test asserts
against, which on 2026-09-30 surfaced as `assert 0 >= 20` in a manifest test
and looked exactly like a real regression.
"""
@pytest.fixture(autouse=True)
def _fresh_session():
    """Every test starts with no baseline, the way a pytest process does."""
    reset_session()
    yield
    reset_session()
def _write(path, body):
    # newline="" so the bytes on disk are exactly `body`: text mode would
    # translate \n to \r\n on Windows, and read_text/read_bytes would then
    # disagree about the same file for reasons that have nothing to do with
    # the guard.
    path.write_text(body, encoding="utf-8", newline="")
    return path
def _rewrite(path, body, *, settle=True):
    """Write again the way another process would: a different size and mtime."""
    before = stamp(path)
    path.write_text(body, encoding="utf-8", newline="")
    if settle:
        # Filesystem timestamps can be coarse enough that a same-length rewrite
        # inside one millisecond keeps the stamp. Force it apart so the test is
        # about the guard's logic, not the clock's resolution.
        new = stamp(path)
        while new == before:
            time.sleep(0.01)
            os.utime(path, ns=(new[0] + 1_000_000, new[0] + 1_000_000))
            new = stamp(path)
    return path
def test_a_stable_file_is_read_and_recorded(tmp_path):
    f = _write(tmp_path / "bridge_core.h", "#define X 1\n")
    assert read_text(f) == "#define X 1\n"
    assert read_text(f) == "#define X 1\n", "a second read of the same bytes is fine"
    assert read_bytes(f) == b"#define X 1\n", "text and bytes share one baseline"
def test_a_file_rewritten_between_two_reads_raises_by_name(tmp_path):
    """The case from 2026-09-30: the file changed, so the assertion is not about
    the code. The message has to carry the path and both stamps - a bare
    AssertionError is what made the original failure cost half an hour."""
    f = _write(tmp_path / "findex_manifest.h", "#define A 1\n")
    read_text(f)                                  # the suite's first read

    _rewrite(f, "#define A 2\n#define B 3\n")
    with pytest.raises(ConcurrentRewrite) as caught:
        read_text(f)
    message = str(caught.value)
    assert "findex_manifest.h" in message, "the note must name the file"
    assert "another process rewrote" in message
    assert "Re-run" in message, "the note must say what to do about it"
    assert "first read by the suite" in message and "now" in message
def test_the_exception_is_an_assertion_error(tmp_path):
    """It has to fail the test, not error it: one line in the test's own report
    instead of a separate ERROR block the reader has to match up by hand."""
    assert issubclass(ConcurrentRewrite, AssertionError)
    f = _write(tmp_path / "a.h", "x")
    read_text(f)
    _rewrite(f, "y")
    with pytest.raises(AssertionError):
        read_text(f)
def test_a_rewrite_after_the_sessions_first_read_is_caught_for_bytes_too(tmp_path):
    """Build outputs go through read_bytes, and a build running alongside the
    tests is the most likely writer of all."""
    f = _write(tmp_path / "farever_dps.dll", "binary-one")
    read_bytes(f)
    _rewrite(f, "binary-two-longer")
    with pytest.raises(ConcurrentRewrite):
        read_bytes(f)
def test_a_torn_read_is_caught_even_when_the_file_never_settles(tmp_path):
    """A writer that truncates and rewrites can hand back half a file, and the
    bytes would look like a perfectly good (wrong) version. The before/after
    pair around the read is what catches that, so it must not depend on the
    session baseline being set up first."""
    f = _write(tmp_path / "manifest.h", "a" * 4096)

    real_read_text = type(f).read_text

    def _rewrite_mid_read(self, *a, **kw):
        body = real_read_text(self, *a, **kw)
        f.write_text("b", encoding="utf-8", newline="")   # the file moves as we read it
        return body

    type(f).read_text = _rewrite_mid_read
    try:
        with pytest.raises(ConcurrentRewrite) as caught:
            read_text(f)
    finally:
        type(f).read_text = real_read_text
    assert "torn read" in str(caught.value)
def test_a_missing_file_still_raises_the_usual_error(tmp_path):
    """Several callers do `if not path.is_file(): pytest.skip(...)` and expect
    the read to raise FileNotFoundError. The guard must not turn a missing
    sibling checkout into a rewrite report."""
    with pytest.raises(FileNotFoundError):
        read_text(tmp_path / "not_checked_out.h")
    with pytest.raises(FileNotFoundError):
        read_bytes(tmp_path / "not_checked_out.dll")
def test_rewrites_between_sessions_are_not_a_signal(tmp_path):
    """The false positive that would make the guard noise: a file edited
    between two runs is just development. The baseline is taken at the FIRST
    read of a session, so it can only ever fire on a move within one."""
    f = tmp_path / "notes.md"
    for body in ("first\n", "second\n", "third\n"):
        _write(f, body)
        reset_session()                 # a new pytest process
        assert read_text(f) == body     # never raises

# ========================================================================
# from tests/test_sound_guard.py
# ========================================================================

"""The alert-sound burst guard in `farever_companion/runtime/sound.py`.

`play_sound` is this app's only audio entry point, and the OS gives it one
channel per process: a second call cancels whatever is sounding and starts the
new tone. For a different alert that is intended; for the SAME alert it is
always a fault, because nothing wants the same tone restarted mid-note — a
caller firing in a loop that way is heard as one long stutter, not an alert.

These tests drive the guard with a fake clock and a recorded playback, so no
sound is played and nothing depends on wall-clock timing (or on `winsound`
existing — it is injected, so the suite is OS-independent).
"""
class _FakeWinsound(types.ModuleType):
    """Stands in for the real module so `play_sound` always gets past import."""

    SND_MEMORY = 4
    SND_NODEFAULT = 2

    def __init__(self) -> None:
        super().__init__("winsound")
        self.buffers: list[bytes] = []

    def PlaySound(self, data, flags=0) -> None:  # noqa: N802 (Win32 name)
        self.buffers.append(bytes(data))
@pytest.fixture
def audio(monkeypatch):
    """A recorder in place of the OS, and a clock the test controls."""
    fake = _FakeWinsound()
    monkeypatch.setitem(sys.modules, "winsound", fake)

    played: list[bytes] = []
    real_wav = sound._play_wav
    monkeypatch.setattr(sound, "_play_wav", lambda data: played.append(data))
    # Playback normally hops to a thread; inline keeps the assertions raceless.
    monkeypatch.setattr(sound, "_play_async", lambda fn: fn())

    clock = {"t": 10_000.0}
    monkeypatch.setattr(sound, "_now", lambda: clock["t"])

    lines: list[str] = []
    sound.set_sound_logger(lines.append)
    sound.reset_sound_state()
    yield types.SimpleNamespace(
        played=played, clock=clock, lines=lines, real_play_wav=real_wav)
    sound.reset_sound_state()
    sound.set_sound_logger(None)
DING_SECONDS = sound._tone_seconds(sound._SOUND_SPECS["ding"])
REPEAT_WINDOW = max(DING_SECONDS, sound._MIN_REPEAT_S)
def test_playback_is_serialized_so_two_alerts_never_overlap(monkeypatch):
    """One worker, one channel: queued alerts play in order, never together.

    The bug this pins is why the rift chimes were reported as "going off 2 at a
    time and not in right order" (field report 2026-10-03). Every guard test
    above patches `_play_async` to run INLINE, which is why they could not see
    it: the guard was always right about WHICH alerts fire and in what order
    they were requested — the race lived one layer down, where each playback
    used to get its own thread and `winsound` arbitrated them arbitrarily.

    So this test deliberately keeps the real `_play_async` and replaces only the
    OS call with a recorder that can observe overlap.
    """
    monkeypatch.setitem(sys.modules, "winsound", _FakeWinsound())
    order: list[str] = []
    busy = threading.Lock()
    overlaps: list[str] = []

    def _fake_play_wav(data: bytes) -> None:
        # Two tones overlapping means the worker ran two playbacks at once.
        if not busy.acquire(blocking=False):
            overlaps.append("clipped")
            return
        try:
            order.append(str(len(data)))
            time.sleep(0.02)            # stands in for the tone's length
        finally:
            busy.release()

    monkeypatch.setattr(sound, "_play_wav", _fake_play_wav)
    monkeypatch.setattr(sound, "_now", lambda: 10_000.0)
    sound.reset_sound_state()

    try:
        for name in ("ding", "chime", "robot", "chime2"):
            # force=True skips the guard so this exercises PLAYBACK order only.
            sound.play_sound(name, force=True)
        sound._AUDIO_QUEUE.join()          # wait for the worker to drain
    finally:
        sound.reset_sound_state()

    assert not overlaps, "two alerts were played at the same time"
    assert len(order) == 4, order         # none dropped either
    # Distinct byte lengths prove each queued alert played its OWN sound, and
    # in the order they were requested.
    assert len(set(order)) == len(order), order
def test_repeated_alert_never_restarts_the_tone(audio):
    """The reported shape: one trigger firing in a loop must sound once."""
    for _ in range(40):
        sound.play_sound("ding")

    assert len(audio.played) == 1
    assert sound.suppressed_counts()["ding"] == 39
def test_an_alert_cannot_stutter_even_just_past_its_own_length(audio):
    """A tone shorter than a second used to be repeatable within it."""
    sound.play_sound("ding")
    audio.clock["t"] += DING_SECONDS + 0.05   # the tone has ended…
    sound.play_sound("ding")                  # …but the alert may not repeat yet
    audio.clock["t"] += 0.5
    sound.play_sound("ding")                  # still inside the 1 s window

    assert len(audio.played) == 1
    assert sound.suppressed_counts()["ding"] == 2
def test_alert_plays_again_once_the_window_has_passed(audio):
    """The guard is a window, not a mute: the next real alert still rings."""
    sound.play_sound("ding")
    audio.clock["t"] += REPEAT_WINDOW + 0.2
    sound.play_sound("ding")

    assert len(audio.played) == 2
    assert "ding" not in sound.suppressed_counts()
def test_different_alerts_fired_back_to_back_are_capped(audio):
    """Two triggers landing on the same tick must not double-beep either."""
    for name in sound.list_sounds() * 3:
        sound.play_sound(name)

    assert len(audio.played) == 1
def test_an_explicit_preview_is_never_suppressed(audio):
    """Clicking through the picker is a user action, not a burst."""
    for _ in range(5):
        sound.play_sound("chime", force=True)

    assert len(audio.played) == 5
    assert sound.suppressed_counts() == {}
def test_a_suppressed_burst_reports_itself(audio):
    """A burst names its own cause in the Activity Log — tone, count, reason."""
    for _ in range(40):
        sound.play_sound("ding")

    assert len(audio.lines) == 1, audio.lines
    line = audio.lines[0]
    assert "ding" in line and "still sounding" in line
def test_a_sustained_burst_keeps_updating_its_count(audio):
    """The report is throttled, not silenced: a longer burst shows its size."""
    burst = 0
    for _ in range(20):          # 0.2 s apart: a caller firing ~5 times a second
        burst += 1
        sound.play_sound("ding")
        audio.clock["t"] += 0.2

    assert len(audio.lines) >= 2, audio.lines
    first = int(audio.lines[0].split("×")[1].split()[0])
    last = int(audio.lines[-1].split("×")[1].split()[0])
    assert last > first, audio.lines
    assert len(audio.played) < burst // 2, f"{len(audio.played)} plays for {burst} calls"
def test_a_clean_alert_logs_nothing(audio):
    """Ordinary alerting stays quiet in the log; only a drop is worth a line."""
    sound.play_sound("ding")
    audio.clock["t"] += REPEAT_WINDOW + 0.2
    sound.play_sound("ding")

    assert audio.lines == []
def test_the_guard_is_shared_by_every_caller(audio):
    """A second caller can't reintroduce the burst the chime rule removes."""
    for _ in range(10):
        sound.play_ding()          # legacy alias path
    for _ in range(10):
        sound.play_sound("ding")   # the chime's path

    assert len(audio.played) == 1
    assert sound.suppressed_counts()["ding"] == 19
def test_unknown_name_falls_back_and_is_guarded_like_ding(audio):
    """An unrecognised key plays `ding` — and must not bypass the guard."""
    for _ in range(6):
        sound.play_sound("not-a-sound")

    assert len(audio.played) == 1
    assert sound.suppressed_counts()["not-a-sound"] == 5
def test_playback_asks_windows_not_to_substitute_its_own_ding(audio, monkeypatch):
    """SND_NODEFAULT: a failed playback must be silent, not a system beep."""
    calls = []
    fake = sys.modules["winsound"]
    monkeypatch.setattr(fake, "PlaySound",
                        lambda data, flags=0: calls.append(flags))

    audio.real_play_wav(b"RIFF")   # the un-stubbed implementation

    assert calls == [fake.SND_MEMORY | fake.SND_NODEFAULT]

# ========================================================================
# from tests/test_crash_log.py
# ========================================================================

"""The dev crash log must timestamp its fatal entries.

`run-log.bat` sets FAREVER_DEV_CRASH_LOG and `run.py` arms faulthandler with it,
so a developer run leaves a stack dump when the process dies on an access
violation — and that dump carries no clock. It cannot be given one from the
write side either: faulthandler writes straight to the file DESCRIPTOR (a file
object whose write() prepends a stamp is never called), and the process is
already dying when it lands. Hence the vectored exception handler in
`farever_companion/runtime/crash_log.py`, and hence the end-to-end test below: the whole
point is that the stamp lands ABOVE the dump, which only a real crash proves.
"""
PROJECT_ROOT = Path(__file__).resolve().parents[1]
def test_stamp_line_carries_the_wall_clock_and_the_pid():
    line = crash_log.stamp_line(datetime(2026, 9, 25, 12, 4, 5))
    assert line.startswith("\n=== fatal exception 2026-09-25 12:04:05 pid=")
    assert line.endswith(" ===\n")
    # The pid is what makes a dump attributable to a run. Without it the owner
    # has to be inferred from the surrounding session markers, and that
    # inference reads the 2026-10-03 12:37 dump as belonging to the session
    # that started at 12:40 - three minutes after it died.
    assert f"pid={os.getpid():08d}" in line
    # Fixed width, always: the handler writes a buffer pre-allocated from this
    # string and rebuilt in place, so a pid whose digit count changed would
    # fail the same-shape check and freeze the stamp at its startup text.
    other = crash_log.stamp_line(datetime(2031, 12, 31, 23, 59, 59))
    assert len(other) == len(line)
def test_session_line_names_the_time_and_the_pid(tmp_path):
    """run.log is appended across sessions; each append needs a boundary."""
    path = tmp_path / "run.log"
    assert crash_log.write_session_marker(str(path)) is True
    body = path.read_text(encoding="utf-8")
    assert f"=== session start {datetime.now():%Y-%m-%d}" in body
    assert f"pid={os.getpid()}" in body
def test_the_log_has_one_writer_and_one_line_ending(tmp_path):
    """Every entry goes through ONE descriptor, with ONE line ending.

    The 2026-09-25 run.log was written by five of them — faulthandler's file
    object, run.py's and app.py's traceback openers, the session marker, and
    the VEH descriptor — and the text-mode ones translated ``\\n`` to
    ``\\r\\n`` while faulthandler and the raw ``WriteFile`` did not, so the file
    mixed the two with no way to tell where an entry began.
    """
    path = tmp_path / "run.log"
    url = str(path)
    assert crash_log.open_dev_log(url) is crash_log.open_dev_log(url)
    assert crash_log.write_session_marker(url) is True
    assert crash_log.write_python_exception(url, "Traceback (most recent call last)\n") is True
    body = path.read_bytes()
    assert b"\r" not in body, "a text-mode writer came back"
    assert body.count(b"=== session start ") == 1
    assert body.count(b"=== Python exception ") == 1
    # Appended in the order they were written: marker, then the traceback.
    assert body.index(b"=== session start ") < body.index(b"=== Python exception ")
def test_only_fatal_exception_codes_are_stamped():
    """A VEH sees every first-chance exception, and most are not crashes.

    Stamping those would fill run.log from a healthy process (C++ throws and
    ctypes' own caught access violations both reach a vectored handler), and a
    stack overflow must not be stamped at all: the handler would run on the
    stack that just blew up and could take faulthandler's dump down with it.
    """
    assert 0xC0000005 in crash_log.STAMPED_EXCEPTION_CODES      # access violation
    assert 0xC00000FD not in crash_log.STAMPED_EXCEPTION_CODES  # stack overflow
    assert 0xE06D7363 not in crash_log.STAMPED_EXCEPTION_CODES  # C++ throw
    assert 0x80000003 not in crash_log.STAMPED_EXCEPTION_CODES  # breakpoint
def test_activity_tail_keeps_the_last_lines_that_fit():
    lines = ["21:17:01  attached pid 42", "21:17:05  DPS capture started",
             "21:17:09  overlay shown"]
    block = crash_log.activity_tail_block(lines, max_chars=200)
    assert block.startswith("\n=== activity tail ===\n")
    assert block.endswith("\n")
    for ln in lines:
        assert ln in block                    # everything fits under 200
    tight = crash_log.activity_tail_block(lines, max_chars=60)
    assert "overlay shown" in tight           # the LAST line always survives
    assert "attached pid 42" not in tight     # the oldest one is dropped first
def test_activity_tail_is_empty_for_no_lines():
    assert crash_log.activity_tail_block([]) == ""
    assert crash_log.activity_tail_block(None) == ""
def test_activity_tail_drops_whole_lines_never_a_middle():
    lines = [f"line {i:02d} " + "x" * 12 for i in range(40)]
    block = crash_log.activity_tail_block(lines, max_chars=400)
    body = block.split("=== activity tail ===\n", 1)[1].rstrip("\n")
    kept = body.split("\n")
    assert kept, block
    # a contiguous suffix: the tail keeps whole lines, in order
    assert kept == lines[-len(kept):]
def test_set_activity_tail_rebuilds_the_buffer():
    crash_log.set_activity_tail(["a", "b"])
    assert crash_log._activity_buffer is not None
    assert b"=== activity tail ===" in crash_log._activity_buffer
    assert b"a\nb" in crash_log._activity_buffer
    crash_log.set_activity_tail([])
    assert crash_log._activity_len == 0      # nothing to write after a clear
_CHILD = r'''
import ctypes, faulthandler, sys
from farever_companion.runtime import crash_log

path = sys.argv[1]
# Exactly what run.py does: ONE descriptor for the session, handed to
# faulthandler, with the stamp installed on top of it.
log = crash_log.open_dev_log(path)
faulthandler.enable(file=log.file(), all_threads=True)
crash_log.install_fatal_stamp(path)      # AFTER enable(): reverse order wins
crash_log.write_session_marker(path)
crash_log.set_activity_tail(["21:17:01  attached pid 42",
                             "21:17:05  DPS capture started"])
print("child armed", flush=True)
ctypes.memmove(0, b"\x41", 1)            # a real access violation
print("NOT REACHED", flush=True)
'''
@pytest.mark.skipif(sys.platform != "win32", reason="VEHs are Windows-only")
def test_a_fatal_dump_lands_under_a_timestamp(tmp_path):
    path = tmp_path / "run.log"
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(path)],
        cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT),
             "QT_QPA_PLATFORM": "offscreen"})
    body = path.read_text(encoding="utf-8", errors="replace")
    assert "child armed" in proc.stdout, (proc.returncode, proc.stdout, proc.stderr)
    assert "=== fatal exception " in body, body[:400]
    # The dump is faulthandler's, and it came AFTER the stamp.
    assert "Current thread" in body, body[:400]
    assert body.index("=== fatal exception ") < body.index("Current thread")
    # One writer: the session marker was appended first (session start), the
    # fatal stamp second (the crash), and nothing wrote a \r anywhere.
    assert body.index("=== session start ") < body.index("=== fatal exception ")
    assert "\r" not in body
    # The activity tail rides under the stamp: what the app was doing is
    # right there beside the dump (the crash cannot write it itself — the
    # fault path only copies the pre-built buffer).
    assert "=== activity tail ===" in body, body[:400]
    assert "DPS capture started" in body
    assert body.index("=== fatal exception ") < body.index("=== activity tail ===")
    assert body.index("=== activity tail ===") < body.index("Current thread")
@pytest.mark.skipif(sys.platform != "win32", reason="VEHs are Windows-only")
def test_installing_the_stamp_twice_registers_one_handler(tmp_path):
    """A second registration would leave a dangling callback behind."""
    if crash_log._registered:       # another test in this process already did it
        pytest.skip("handler already registered in this process")
    path = tmp_path / "run.log"
    first = crash_log.install_fatal_stamp(str(path))
    assert first is not None
    assert crash_log.install_fatal_stamp(str(path)) is None
    assert crash_log._registered == [first]   # kept alive, and only once
@pytest.mark.skipif(sys.platform != "win32", reason="VEHs are Windows-only")
def test_the_stamp_payload_is_rebuilt_with_the_current_clock(tmp_path):
    """The stamp's clock must not be frozen at install time.

    It used to be: the payload was built once, when the handler was installed,
    so a crash's stamp reported the SESSION START — the 2026-09-25 run.log
    shows ``=== fatal exception 14:22:28 ===`` sitting beside ``=== session
    start 14:22:28 pid=8636 ===``, the same second, because both were produced
    at startup. The 1 Hz clock thread (never the fault path, which must not
    allocate) rebuilds the buffer in place instead.
    """
    if crash_log._stamp_buffer is None:
        crash_log.install_fatal_stamp(str(tmp_path / "run.log"))
    if crash_log._stamp_buffer is None:
        pytest.skip("no stamp buffer in this process")
    size = len(crash_log._stamp_buffer.raw)
    payload = crash_log.refresh_stamp_payload(datetime(2030, 1, 2, 3, 4, 5))
    assert b"2030-01-02 03:04:05" in payload
    # In place: the handler's buffer is the same fixed-size object, so the
    # fault path still writes a pre-allocated value with no allocation.
    assert crash_log._stamp_buffer.raw == payload
    assert len(crash_log._stamp_buffer.raw) == size

# ========================================================================
# from tests/test_session_audit.py
# ========================================================================

"""Tests for the session-lifecycle audit (app.py, 2026-09-19).

A clean exit writes a `shutdown` audit line for its pid, a crash writes
`crash`, and the next startup reports `prev-session=<state>` — so an UNCLEAN
kill (closed console window, power loss, hard crash with no excepthook) is
visible as the absence of an end line for the previous pid. This is the
forensic answer to "did the app close itself or was it killed?".
"""
pytest.importorskip("PySide6")
def _log_lines(tmp_path) -> list[str]:
    p = tmp_path / "settings_audit.log"
    if not p.exists():
        return []
    return p.read_text(encoding="utf-8").splitlines()
@pytest.fixture
def audit_env(tmp_path, monkeypatch):
    """Redirect the audit log into the test's temp dir."""
    from farever_companion.config import store as cfg_store
    # One patch seam covers reader and writer both: config.store owns the path
    # resolution AND the audit writer, and app.py's local import comes from
    # there too (the package hub just re-exports the same objects).
    monkeypatch.setattr(cfg_store, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_store, "_audit_path",
                        lambda: tmp_path / "settings_audit.log")
    monkeypatch.setenv("FAREVER_SESSION_AUDIT", "1")
    yield tmp_path
def test_prev_state_unknown_with_empty_log(audit_env):
    assert appmod._prev_session_end_state() == "unknown"
def test_audit_is_disabled_without_explicit_opt_in(tmp_path, monkeypatch):
    from farever_companion.config import store as cfg_store
    monkeypatch.setattr(cfg_store, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg_store, "_audit_path",
                        lambda: tmp_path / "settings_audit.log")
    monkeypatch.delenv("FAREVER_SESSION_AUDIT", raising=False)
    audit_settings_event("save", None, "should-not-write")
    assert not (tmp_path / "settings_audit.log").exists()
def test_prev_state_unknown_when_no_prior_startup(audit_env):
    audit_settings_event("save", None, "x")
    assert appmod._prev_session_end_state() == "unknown"
def test_prev_state_clean_when_prior_session_logged_shutdown(audit_env):
    audit_settings_event("startup", None, "file=x")   # pid = this test's pid
    audit_settings_event("shutdown", None, "session=end")
    assert appmod._prev_session_end_state() == "unknown"  # own pid ignored
def test_prev_state_unclean_when_prior_startup_has_no_end_line(audit_env,
                                                                 monkeypatch):
    """The core scenario: a previous pid logged startup but was killed before
    any shutdown/crash line — the next launch must report unclean."""
    real_pid = os.getpid()
    monkeypatch.setattr(appmod.os, "getpid", lambda: 999999)
    audit_settings_event("startup", None, "file=x")   # pid = 999999 (fake)

    # The "next launch" runs under a different pid (restore the real one —
    # note getpid is patched on the shared os module, so capture the real
    # value first rather than calling the patched function).
    monkeypatch.setattr(appmod.os, "getpid", lambda: real_pid)
    assert appmod._prev_session_end_state() == "unclean"

    # A shutdown from MY pid doesn't explain session 999999's ending...
    audit_settings_event("shutdown", None, "session=end")   # pid = real
    assert appmod._prev_session_end_state() == "unclean"

    # ...but 999999 itself logging shutdown flips it to clean.
    monkeypatch.setattr(appmod.os, "getpid", lambda: 999999)
    audit_settings_event("shutdown", None, "session=end")
    monkeypatch.setattr(appmod.os, "getpid", lambda: real_pid)
    assert appmod._prev_session_end_state() == "clean"
def test_prev_state_clean_when_prior_session_crashed_but_audited(audit_env,
                                                                 monkeypatch):
    """A caught crash (excepthook ran) still counts as an explained end."""
    real_pid = os.getpid()
    monkeypatch.setattr(appmod.os, "getpid", lambda: 888888)
    audit_settings_event("startup", None, "file=x")
    audit_settings_event("crash", None, "session=end RuntimeError: boom")
    monkeypatch.setattr(appmod.os, "getpid", lambda: real_pid)
    # The crashed session is the last startup pid; crash is an end line.
    assert appmod._prev_session_end_state() == "clean"
def test_session_end_line_written_once_and_carry_pid(audit_env):
    appmod._SESSION_AUDITED = False
    appmod._audit_session_end("shutdown", "normal")
    appmod._audit_session_end("shutdown", "normal")   # idempotent
    lines = [ln for ln in _log_lines(audit_env)
             if "shutdown" in ln and "session=end" in ln]
    assert len(lines) == 1
    assert f"pid={os.getpid()}" in lines[0]
    appmod._SESSION_AUDITED = False                   # restore for other tests
def test_crash_after_shutdown_is_still_recorded(audit_env):
    """The once-only latch applies to shutdown; a real crash afterwards must
    not be swallowed by it (different event = different evidence)."""
    appmod._SESSION_AUDITED = False
    appmod._audit_session_end("shutdown")
    appmod._audit_session_end("crash", "RuntimeError: late")
    lines = _log_lines(audit_env)
    assert any("shutdown" in ln and "session=end" in ln for ln in lines)
    assert any("crash" in ln and "RuntimeError" in ln for ln in lines)
    appmod._SESSION_AUDITED = False
def test_crash_audithook_chains_and_writes(monkeypatch, audit_env):
    """_install_crash_audithook: uncaught exception -> crash line + prior
    hook still invoked."""
    appmod._SESSION_AUDITED = False
    seen = []
    monkeypatch.setattr(appmod.sys, "excepthook",
                        lambda et, ev, tb: seen.append(et))
    appmod._install_crash_audithook()
    try:
        appmod.sys.excepthook(ValueError, ValueError("boom"), None)
    finally:
        appmod._SESSION_AUDITED = False
    assert seen and seen[0] is ValueError
    assert any("crash" in ln and "ValueError" in ln
               for ln in _log_lines(audit_env))
def test_shutdown_connection_fires_on_about_to_quit(audit_env, monkeypatch):
    """The wiring: an aboutToQuit emission produces the shutdown line."""
    appmod._SESSION_AUDITED = False
    app_obj = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    app_obj.aboutToQuit.connect(lambda: appmod._audit_session_end("shutdown"))
    try:
        # aboutToQuit only fires when a RUNNING event loop exits — quit()
        # alone with no exec() never emits it. Schedule quit from inside the
        # loop, then let exec() run and return.
        QtCore.QTimer.singleShot(0, app_obj.quit)
        app_obj.exec()
    finally:
        appmod._SESSION_AUDITED = False
    assert any("shutdown" in ln and "session=end" in ln
               for ln in _log_lines(audit_env))

# ========================================================================
# from tests/test_config_flavor.py
# ========================================================================

"""App-vs-tooling data folders: frozen builds use the release folder.

A frozen build uses ``<exe>/moddata``. Source-tree launches (``run.py`` via
``run.bat`` or ``run-log.bat``), pytest, dev tools, and stray imports all use
``dist/dev-moddata`` unless ``FAREVER_MODDATA_DIR`` explicitly overrides the
path. The source launchers therefore cannot write the main user-data folder
by accident.
"""
def _reset_frozen(monkeypatch, root, frozen: bool, exe_name="FareverPal.exe"):
    """Point config.store's process detection at a fake exe root.

    config.store reads the real ``sys`` module inside config_dir() (imported
    locally), so patch sys.frozen/sys.executable globally and undo after.
    The dev root derives from store.py's __file__
    (config/store.py -> parents[2]/"dist"), so for dev-flavor assertions the
    module file is repointed into root too. The session conftest pins
    FAREVER_MODDATA_DIR — these tests exercise the RESOLUTION path, so the
    override is removed first.
    """
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "executable", str(root / exe_name), raising=False)
    monkeypatch.delenv("FAREVER_MODDATA_DIR", raising=False)
    if not frozen:
        # config/store.py does __file__.parents[2] / "dist" — the real file
        # sits at <repo>/farever_companion/config/store.py, so the stub mirrors
        # that depth: <root>/farever_companion/config/store.py -> root.
        fake_cfg = root / "farever_companion" / "config" / "store.py"
        fake_cfg.parent.mkdir(parents=True, exist_ok=True)
        fake_cfg.write_text("# stub\n", encoding="utf-8")
        monkeypatch.setattr(config.store, "__file__", str(fake_cfg))
def test_frozen_exe_uses_moddata_next_to_itself(tmp_path, monkeypatch):
    _reset_frozen(monkeypatch, tmp_path, frozen=True)
    d = config.config_dir()
    assert d == tmp_path / "moddata"
def test_source_tree_app_run_uses_dist_dev_moddata(tmp_path, monkeypatch):
    _reset_frozen(monkeypatch, tmp_path, frozen=False)
    monkeypatch.delenv("FAREVER_DATA_FLAVOR", raising=False)
    d = config.config_dir()
    assert d == tmp_path / "dist" / "dev-moddata"
def test_tooling_defaults_to_dev_moddata(tmp_path, monkeypatch):
    """A process that never says 'I am the app' (pytest, a tool, a stray
    import) must land in dev-moddata — never in the user's moddata."""
    _reset_frozen(monkeypatch, tmp_path, frozen=False)
    monkeypatch.delenv("FAREVER_DATA_FLAVOR", raising=False)
    d = config.config_dir()
    assert d == tmp_path / "dist" / "dev-moddata"
def test_app_and_tooling_never_write_each_others_folder(tmp_path, monkeypatch):
    """The concrete hazard: a frozen app and a source/tooling process use
    separate folders, so each one's settings.json remains its own."""
    _reset_frozen(monkeypatch, tmp_path, frozen=True)
    app_dir = config.config_dir()
    (app_dir / "settings.json").write_text(json.dumps({"who": "app"}),
                                           encoding="utf-8")

    _reset_frozen(monkeypatch, tmp_path, frozen=False)
    monkeypatch.delenv("FAREVER_DATA_FLAVOR", raising=False)
    tool_dir = config.config_dir()
    (tool_dir / "settings.json").write_text(json.dumps({"who": "tool"}),
                                            encoding="utf-8")

    assert app_dir != tool_dir
    assert json.loads((app_dir / "settings.json").read_text())["who"] == "app"
    assert json.loads((tool_dir / "settings.json").read_text())["who"] == "tool"
def test_flavor_flag_is_not_enough_when_frozen(tmp_path, monkeypatch):
    """The frozen exe is an app regardless of the flag; a tooling process is
    tooling even if a stale flag from another shell is lying around."""
    _reset_frozen(monkeypatch, tmp_path, frozen=True)
    monkeypatch.delenv("FAREVER_DATA_FLAVOR", raising=False)
    assert config.config_dir() == tmp_path / "moddata"
def test_first_frozen_run_adopts_legacy_frozen_folder(tmp_path, monkeypatch):
    """Frozen builds before 2026-09-20 wrote <exe>/moddata-frozen. The first
    run of the merged layout renames it so a release user's data is not
    stranded after upgrading."""
    legacy = tmp_path / "moddata-frozen"
    legacy.mkdir()
    (legacy / "settings.json").write_text(json.dumps({"dps_mode": "memory"}),
                                          encoding="utf-8")
    (legacy / "progress_Kohan_Rogue.json").write_text("{}", encoding="utf-8")

    _reset_frozen(monkeypatch, tmp_path, frozen=True)
    d = config.config_dir()

    assert d == tmp_path / "moddata"
    assert json.loads((d / "settings.json").read_text())["dps_mode"] == "memory"
    assert not legacy.exists()          # renamed, not copied-and-left
def test_frozen_run_skips_empty_legacy_folder(tmp_path, monkeypatch):
    """A moddata-frozen WITHOUT settings.json is not user data: don't adopt."""
    legacy = tmp_path / "moddata-frozen"
    legacy.mkdir()
    (legacy / "dps").mkdir()

    _reset_frozen(monkeypatch, tmp_path, frozen=True)
    d = config.config_dir()
    assert d == tmp_path / "moddata"
    assert not d.exists() or not any(d.iterdir())   # nothing adopted
    assert legacy.is_dir()                          # untouched
def test_frozen_run_with_own_folder_ignores_legacy(tmp_path, monkeypatch):
    """Second-and-later frozen runs: own folder exists -> legacy folder is
    left strictly alone."""
    own = tmp_path / "moddata"
    own.mkdir()
    (own / "settings.json").write_text(json.dumps({"who": "current"}),
                                       encoding="utf-8")
    legacy = tmp_path / "moddata-frozen"
    legacy.mkdir()
    (legacy / "settings.json").write_text(json.dumps({"who": "old-frozen"}),
                                          encoding="utf-8")

    _reset_frozen(monkeypatch, tmp_path, frozen=True)
    d = config.config_dir()
    assert d == own
    assert json.loads((own / "settings.json").read_text())["who"] == "current"
    assert json.loads((legacy / "settings.json").read_text())["who"] == "old-frozen"
def test_explicit_override_beats_everything(tmp_path, monkeypatch):
    """FAREVER_MODDATA_DIR still wins for tooling/tests, frozen or not."""
    _reset_frozen(monkeypatch, tmp_path, frozen=True)
    override = tmp_path / "custom"
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(override))   # after reset
    assert config.config_dir() == override

# ========================================================================
# from tests/test_copy_utils.py
# ========================================================================

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
def test_copy_text_writes_formatted_content():
    _app()
    assert copy_text("DPS: 123.4") is True
    assert QtWidgets.QApplication.clipboard().text() == "DPS: 123.4"
def test_copy_with_feedback_restores_button_text():
    app = _app()
    button = QtWidgets.QPushButton("COPY")
    assert copy_with_feedback("loadout", button, copied_text="✓ COPIED", restore_ms=0)
    assert button.text() == "✓ COPIED"
    QtTest.QTest.qWait(10)
    assert button.text() == "COPY"

# ========================================================================
# from tests/test_master_backup.py
# ========================================================================

"""Unit tests for master backup persistence, 5-slot rotation, and data loss recovery."""
@pytest.fixture(autouse=True)
def _moddata(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    yield
def test_snapshot_creation_and_rotation_limit():
    cdir = mb.config_dir()
    
    # Setup initial mock profile and collection
    atomic_write_json(cdir / "collection.json", {"pets": ["p1", "p2"], "mounts": ["m1"], "gliders": []})
    atomic_write_json(cdir / "progress_Paladin.json", {"poi_done": ["poi_1", "poi_2"], "entity_hidden_units": ["m1"]})
    
    # 1. Create first snapshot
    added = mb.create_snapshot_if_changed(max_backups=5)
    assert added is True
    
    data = mb.load_master_backup()
    assert len(data["backups"]) == 1
    snap1 = data["backups"][0]
    assert snap1["collection"]["pets"] == ["p1", "p2"]
    assert "Paladin" in snap1["profiles"]
    
    # 2. No changes -> skip snapshot
    assert mb.create_snapshot_if_changed(max_backups=5) is False
    assert len(mb.load_master_backup()["backups"]) == 1
    
    # 3. Add multiple snapshots to verify 5-slot rotation cap
    for i in range(10):
        # modify data so change is detected
        atomic_write_json(cdir / "progress_Paladin.json", {"poi_done": [f"poi_{i}"], "entity_hidden_units": []})
        mb.create_snapshot_if_changed(max_backups=5)
        
    final_data = mb.load_master_backup()
    assert len(final_data["backups"]) == 5
def test_oldest_snapshot_is_pinned_never_rotates_away():
    """The incident: the only good pre-loss snapshot rotated away five exits
    after the loss. The oldest snapshot must survive every rotation, so a
    later wipe always has a healthy copy to recover from."""
    cdir = mb.config_dir()
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1", "p2"], "mounts": [], "gliders": []})
    atomic_write_json(cdir / "progress_Mage.json",
                      {"poi_done": [f"poi_{i}" for i in range(10)],
                       "entity_hidden_units": []})
    mb.create_snapshot_if_changed(max_backups=5)
    first = mb.load_master_backup()["backups"][0]

    # many healthy exits after -> the first snapshot must survive
    for i in range(8):
        atomic_write_json(cdir / "progress_Mage.json",
                          {"poi_done": [f"poi_{i}"], "entity_hidden_units": []})
        mb.create_snapshot_if_changed(max_backups=5)
    snaps = mb.load_master_backup()["backups"]
    assert len(snaps) == 5
    assert snaps[-1] == first          # oldest pinned

    # a wipe still has the pre-loss copy to merge from
    atomic_write_json(cdir / "progress_Mage.json",
                      {"poi_done": ["poi_1"], "entity_hidden_units": []})
    notes = mb.restore_missing_from_snapshot(first)
    assert any("Mage" in n for n in notes)
    on_disk = json.loads((cdir / "progress_Mage.json").read_text(encoding="utf-8"))
    assert len(on_disk["poi_done"]) == 10
def test_data_loss_detection():
    cdir = mb.config_dir()
    
    # Baseline with 10 POIs and 10 pets
    atomic_write_json(cdir / "collection.json", {"pets": [f"pet_{i}" for i in range(10)], "mounts": [], "gliders": []})
    atomic_write_json(cdir / "progress_Mage.json", {"poi_done": [f"poi_{i}" for i in range(10)], "entity_hidden_units": []})
    
    mb.create_snapshot_if_changed(max_backups=5)
    
    # Normal minor update (1 POI uncheck = 10% change) -> no loss warning
    atomic_write_json(cdir / "progress_Mage.json", {"poi_done": [f"poi_{i}" for i in range(9)], "entity_hidden_units": []})
    assert mb.check_for_data_loss(threshold_pct=20.0) is None
    
    # Big wipe (drop from 10 to 3 POIs = 70% loss) -> flagged!
    atomic_write_json(cdir / "progress_Mage.json", {"poi_done": ["poi_1", "poi_2", "poi_3"], "entity_hidden_units": []})
    loss = mb.check_for_data_loss(threshold_pct=20.0)
    assert loss is not None
    assert loss["has_loss"] is True
    assert any("Mage" in r for r in loss["reasons"])
    
    # Missing profile entirely -> flagged!
    (cdir / "progress_Mage.json").unlink()
    loss = mb.check_for_data_loss(threshold_pct=20.0)
    assert loss is not None
    assert any("missing" in r for r in loss["reasons"])
def test_lossy_newest_snapshot_does_not_mask_loss():
    """Regression: a snapshot written by a session that ALREADY lost data
    must not hide the loss. Detection compares live data against the
    historical maximum across ALL snapshots, never just the newest one -
    otherwise a mid-session wipe followed by a normal exit bakes the loss
    into snapshot[0] and the recovery dialog never appears."""
    cdir = mb.config_dir()

    # 1. Healthy state, snapshot holds 10 POIs
    atomic_write_json(cdir / "progress_Mage.json",
                      {"poi_done": [f"poi_{i}" for i in range(10)],
                       "entity_hidden_units": []})
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1"], "mounts": [], "gliders": []})
    mb.create_snapshot_if_changed(max_backups=5)
    assert mb.check_for_data_loss(threshold_pct=20.0) is None

    # 2. Wipe happens mid-session; the exit then snapshots the LOSSY state
    #    (this is exactly what happened: the wipe's own session wrote the
    #    newest snapshot already containing the loss)
    atomic_write_json(cdir / "progress_Mage.json",
                      {"poi_done": ["poi_1", "poi_2"],
                       "entity_hidden_units": []})
    mb.create_snapshot_if_changed(max_backups=5)
    assert len(mb.load_master_backup()["backups"]) == 2

    # 3. Live file is still the lossy one - detection MUST flag it even
    #    though the newest snapshot matches the lossy live state exactly
    loss = mb.check_for_data_loss(threshold_pct=20.0)
    assert loss is not None
    assert any("Mage" in r and "10" in r for r in loss["reasons"])
    # recovery points at the fullest (healthy) snapshot, not the lossy one
    assert loss["latest_snapshot"]["profiles"]["Mage"]["poi_done"] \
        == [f"poi_{i}" for i in range(10)]
def test_restore_from_snapshot():
    cdir = mb.config_dir()
    
    # Create backup state
    atomic_write_json(cdir / "collection.json", {"pets": ["p1", "p2"], "mounts": [], "gliders": []})
    atomic_write_json(cdir / "progress_Warrior.json", {"poi_done": ["w1", "w2", "w3"], "entity_hidden_units": []})
    atomic_write_json(cdir / "progress_Priest.json", {"poi_done": ["pr1"], "entity_hidden_units": []})
    
    mb.create_snapshot_if_changed(max_backups=5)
    snap = mb.load_master_backup()["backups"][0]
    
    # Simulate wipe of Priest only
    (cdir / "progress_Priest.json").unlink()
    
    # Single profile restore
    res = mb.restore_from_snapshot(snap, target_profile="Priest")
    assert res is True
    assert (cdir / "progress_Priest.json").exists()
    assert json.loads((cdir / "progress_Priest.json").read_text(encoding="utf-8"))["poi_done"] == ["pr1"]
    
    # Wipe entire directory
    for f in cdir.glob("*.json"):
        if f.name != "master_backup.json":
            f.unlink()
            
    # Full restore
    res_full = mb.restore_from_snapshot(snap)
    assert res_full is True
    assert (cdir / "collection.json").exists()
    assert (cdir / "progress_Warrior.json").exists()
    assert (cdir / "progress_Priest.json").exists()
def test_restore_missing_merges_only_missing_collection():
    cdir = mb.config_dir()
    # snapshot holds 10 pets; current lost 4 of them but gained 1 new
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1", "p2", "p3"], "mounts": ["m1"], "gliders": []})
    atomic_write_json(cdir / "progress_Mage.json",
                      {"poi_done": ["a"], "entity_hidden_units": []})
    mb.create_snapshot_if_changed(max_backups=5)
    snap = mb.load_master_backup()["backups"][0]
    bak_pets = set(snap["collection"]["pets"])

    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1", "p2", "p3", "p_new"], "mounts": ["m1"],
                       "gliders": []})
    # simulate the reported loss: only a few remain
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1", "p_new"], "mounts": ["m1"], "gliders": []})

    notes = mb.restore_missing_from_snapshot(snap)
    assert any("Collection pets" in n for n in notes)
    merged = json.loads((cdir / "collection.json").read_text(encoding="utf-8"))
    # current p_new is preserved; all backup pets restored
    assert "p_new" in merged["pets"]
    assert bak_pets <= set(merged["pets"])

    # idempotent: a second run has nothing left to add
    assert mb.restore_missing_from_snapshot(snap) == []
def test_restore_missing_preserves_profile_fields_and_adds_only_missing():
    cdir = mb.config_dir()
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1"], "mounts": [], "gliders": []})
    atomic_write_json(cdir / "progress_Warrior.json",
                      {"poi_done": ["w1", "w2", "w3"],
                       "entity_hidden_units": [], "waypoints": ["keepme"],
                       "speedrun_best": {"zone": 12.3}})
    mb.create_snapshot_if_changed(max_backups=5)
    snap = mb.load_master_backup()["backups"][0]

    # current file dropped w2 and w3 but still has waypoints/speedrun fields
    atomic_write_json(cdir / "progress_Warrior.json",
                      {"poi_done": ["w1"], "entity_hidden_units": [],
                       "waypoints": ["keepme"], "speedrun_best": {"zone": 9.9}})

    notes = mb.restore_missing_from_snapshot(snap)
    assert any("Warrior" in n for n in notes)
    cur = json.loads((cdir / "progress_Warrior.json").read_text(encoding="utf-8"))
    assert set(cur["poi_done"]) == {"w1", "w2", "w3"}
    # untouched non-merge fields survive byte-for-byte
    assert cur["waypoints"] == ["keepme"]
    assert cur["speedrun_best"] == {"zone": 9.9}   # newer best NOT downgraded
def test_restore_missing_recreates_missing_profile_and_merges_planner():
    cdir = mb.config_dir()
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1"], "mounts": [], "gliders": []})
    atomic_write_json(cdir / "progress_Priest.json",
                      {"poi_done": ["pr1", "pr2"], "entity_hidden_units": []})
    atomic_write_json(cdir / "planner.json", {"farm": [{"id": "a"}, "b"]})
    mb.create_snapshot_if_changed(max_backups=5)
    snap = mb.load_master_backup()["backups"][0]

    # Priest profile gone; planner lost item b
    (cdir / "progress_Priest.json").unlink()
    atomic_write_json(cdir / "planner.json", {"farm": [{"id": "a"}, "c"]})

    notes = mb.restore_missing_from_snapshot(snap)
    assert any("Priest" in n and "missing" in n for n in notes)
    assert any("farm" in n.lower() for n in notes)
    assert (cdir / "progress_Priest.json").exists()
    plan = json.loads((cdir / "planner.json").read_text(encoding="utf-8"))
    ids = [x["id"] if isinstance(x, dict) else x for x in plan["farm"]]
    assert "a" in ids and "b" in ids and "c" in ids   # merged, nothing removed
def test_restore_missing_nothing_to_do_when_data_intact():
    cdir = mb.config_dir()
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1", "p2"], "mounts": ["m1"], "gliders": []})
    atomic_write_json(cdir / "progress_Mage.json",
                      {"poi_done": ["a", "b"], "entity_hidden_units": []})
    mb.create_snapshot_if_changed(max_backups=5)
    snap = mb.load_master_backup()["backups"][0]
    # live files match the snapshot - a false-positive loss check should merge nothing
    assert mb.restore_missing_from_snapshot(snap) == []
def test_save_collection_never_wipes_populated_file(tmp_settings_dir=None):
    import farever_companion.config as cfg
    cdir = mb.config_dir()
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1", "p2"], "mounts": [], "gliders": []})
    s = cfg.Settings.load()
    assert s.pet_hidden_units == ["p1", "p2"]
    # simulate the startup case: in-memory lists are empty, disk is populated
    s.pet_hidden_units = []
    s.mount_hidden_units = []
    s.glider_hidden_units = []
    s.save_collection()
    on_disk = json.loads((cdir / "collection.json").read_text(encoding="utf-8"))
    assert on_disk["pets"] == ["p1", "p2"]     # not clobbered by the empty state
def _full_prof(pois: int, orbs: int = 0, hidden: int = 0) -> dict:
    return {
        "poi_done": [f"poi_{i}" for i in range(pois)],
        "dungeon_orb_done": [f"orb_{i}" for i in range(orbs)],
        "entity_hidden_units": [f"u_{i}" for i in range(hidden)],
    }
def test_dismiss_profile_alert_silences_unwanted_profile():
    """When a user dismisses a profile alert, it is cleared from guards, added to
    ignored_profiles, and check_for_data_loss no longer warns about it."""
    cdir = mb.config_dir()
    atomic_write_json(cdir / "progress_TempChar.json", _full_prof(50))
    mb.create_snapshot_if_changed(max_backups=5)

    # User deletes the unwanted profile
    (cdir / "progress_TempChar.json").unlink()
    loss = mb.check_for_data_loss()
    assert loss is not None
    assert any("TempChar" in r for r in loss["reasons"])

    # User clicks "Don't ask again" / forgets the profile
    mb.dismiss_profile_alert("TempChar", purge_from_backups=True)
    loss2 = mb.check_for_data_loss()
    assert loss2 is None or not any("TempChar" in r for r in (loss2.get("reasons") or []))
def test_accept_current_moddata_state_clears_all_warnings():
    """Accepting current state silences both reduced counts and missing profiles."""
    cdir = mb.config_dir()
    atomic_write_json(cdir / "progress_CharA.json", _full_prof(40))
    atomic_write_json(cdir / "progress_CharB.json", _full_prof(20))
    mb.create_snapshot_if_changed(max_backups=5)

    # Delete CharB and reduce CharA
    (cdir / "progress_CharB.json").unlink()
    atomic_write_json(cdir / "progress_CharA.json", _full_prof(5))

    loss = mb.check_for_data_loss()
    assert loss is not None

    # User accepts current state (Skip / Keep Current)
    mb.accept_current_moddata_state()
    loss_after = mb.check_for_data_loss()
    assert loss_after is None
_GOOD_SETTINGS = {"open_overlays": ["dps", "dungeon", "entity", "map", "speedrun"],
                  "geometry": {"dps": "34,569,320,420", "dungeon": "7,134,354,214"}}
def _seed_healthy_state():
    cdir = mb.config_dir()
    atomic_write_json(cdir / "settings.json", _GOOD_SETTINGS)
    atomic_write_json(cdir / "collection.json",
                      {"pets": ["p1", "p2"], "mounts": ["m1"], "gliders": []})
    atomic_write_json(cdir / "progress_Mage.json",
                      {"poi_done": ["poi_1"], "entity_hidden_units": []})
    assert mb.create_snapshot_if_changed() is True
def test_snapshot_refuses_to_record_a_settings_wipe():
    """The incident: after a stray writer empties overlay intent, the newest
    snapshot must still hold the good list - a wipe never becomes "truth".

    Two shapes pinned: with other data also changed a new snapshot IS created
    carrying the reference settings; with only settings wiped, the snapshotter
    sees the refused state as identical to the newest snapshot and adds
    nothing (the good list is already the newest truth - equally correct)."""
    _seed_healthy_state()

    # the stray writer's damage, plus a normal collection change so a
    # snapshot is genuinely warranted
    atomic_write_json(mb.config_dir() / "settings.json",
                      {"open_overlays": [], "geometry": {}})
    atomic_write_json(mb.config_dir() / "collection.json",
                      {"pets": ["p1", "p2", "p3"], "mounts": ["m1"], "gliders": []})

    assert mb.create_snapshot_if_changed() is True
    newest = mb.load_master_backup()["backups"][0]
    assert newest["settings"]["open_overlays"] == _GOOD_SETTINGS["open_overlays"]
    assert newest["settings"]["geometry"] == _GOOD_SETTINGS["geometry"]
    assert newest["collection"]["pets"] == ["p1", "p2", "p3"]

    # wipe-only shape: refusal swaps the reference in, content matches the
    # newest snapshot, so nothing new is added and nothing degrades
    atomic_write_json(mb.config_dir() / "settings.json",
                      {"open_overlays": [], "geometry": {}})
    assert mb.create_snapshot_if_changed() is False
    assert mb.load_master_backup()["backups"][0]["settings"]["open_overlays"] \
        == _GOOD_SETTINGS["open_overlays"]
def test_wiped_intent_is_reported_at_startup():
    """check_for_data_loss surfaces the settings wipe and points recovery at
    the backup snapshots (the real incident sequence: wipe -> app exits ->
    next launch)."""
    _seed_healthy_state()
    atomic_write_json(mb.config_dir() / "settings.json",
                      {"open_overlays": [], "geometry": {}})
    mb.create_snapshot_if_changed()      # the exit snapshot: refusal runs here

    info = mb.check_for_data_loss()
    assert info and info["has_loss"]
    assert any("overlay restore list" in r.lower() for r in info["reasons"])
    # recovery points at the fullest (healthy) snapshot
    assert info["latest_snapshot"]["settings"]["open_overlays"] \
        == _GOOD_SETTINGS["open_overlays"]
def test_closing_some_huds_is_normal_use_not_loss():
    """The false-positive guard: 5 -> 3 overlays with geometry intact is a
    user closing HUDs, and the snapshot must record it verbatim."""
    _seed_healthy_state()
    shrunk = {"open_overlays": ["dps", "entity"],
              "geometry": {"dps": "34,569,420,420"}}
    atomic_write_json(mb.config_dir() / "settings.json", shrunk)

    assert mb.create_snapshot_if_changed() is True
    newest = mb.load_master_backup()["backups"][0]
    assert newest["settings"]["open_overlays"] == ["dps", "entity"]

    # and nothing trips at startup either
    assert mb.check_for_data_loss() is None
def test_keep_current_accepts_a_deliberate_full_close():
    """The escape hatch: a user who closed every HUD on purpose clicks
    Keep Current, and the alert never comes back."""
    _seed_healthy_state()
    atomic_write_json(mb.config_dir() / "settings.json",
                      {"open_overlays": [], "geometry": {}})
    assert mb.check_for_data_loss() is not None

    mb.accept_current_moddata_state()
    assert mb.check_for_data_loss() is None

# ========================================================================
# from tests/test_updater.py
# ========================================================================

"""App lifecycle: updater logic and page-cache eviction (headless Qt)."""
def test_parse_version_strips_v_and_suffix():
    assert parse_version("v0.1.2") == (0, 1, 2)
    assert parse_version("0.1.2-beta3") == (0, 1, 2, 3)
    assert parse_version("") == (0,)
    assert parse_version("garbage") == (0,)
def test_is_newer():
    assert is_newer("0.1.2", "0.1.1") is True
    assert is_newer("0.2.0", "0.1.9") is True
    assert is_newer("1.0", "0.9.9") is True
    assert is_newer("0.1.1", "0.1.1") is False      # equal is not newer
    assert is_newer("0.1.0", "0.1.1") is False      # older
    assert is_newer("v0.1.2", "0.1.1") is True      # tolerates a leading v
class _FakeAPI:
    def __init__(self, payload):
        self._payload = payload

    def latest_release(self):
        return self._payload


#: A release asset URL shaped like the one the project actually publishes (a
#: GitHub release download). `check` holds `url` to the host trust list now, so a
#: fixture naming an arbitrary host is refused before the assertions below.
_TRUSTED_ASSET = ("https://github.com/kohan-mucan/FareverPal/releases/download/"
                  "v9.9.9/FareverPal.exe")
def _bump_major(v: str) -> str:
    parts = list(parse_version(v))
    parts[0] += 1
    return ".".join(str(p) for p in parts)
def test_check_returns_info_when_newer():
    bumped = _bump_major(updater.current_version())
    info = updater.check(_FakeAPI({
        "ok": True, "version": bumped, "url": _TRUSTED_ASSET,
        "notes": "n", "sha256": "abc", "size": 10,
    }))
    assert isinstance(info, UpdateInfo)
    assert info.version == bumped
    assert info.url == _TRUSTED_ASSET
    assert info.sha256 == "abc"
    assert info.size == 10


def test_check_rejects_a_payload_with_no_sha256():
    """The release sha256 (the hash the binary is installed and relaunched with) is
    now required. Without it the payload is treated as unreadable — "nothing newer"
    — so a row that arrived from a newer build (or a dropped field) is not handed to
    a pill that asks the player to install an unhashed binary.
    """
    for variant in (
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET},
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
         "sha256": None},
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
         "sha256": ""},
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
         "sha256": "   "},
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
         "sha256": 7, "size": None},
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
         "sha256": [], "size": None},
    ):
        got = updater.check(_FakeAPI(variant))
        assert got is None, (variant, got)


def test_check_rejects_a_payload_with_no_size():
    """A payload missing its size is still installed, but only after the sha256
    check passes. size is optional for the progress bar, mandatory for the hash.
    """
    got = updater.check(_FakeAPI({
        "ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
        "sha256": "abc", "size": None,
    }))
    assert isinstance(got, updater.UpdateInfo), got
    assert got.size is None
    assert got.sha256 == "abc"


def test_check_none_when_same_version():
    assert updater.check(_FakeAPI({
        "ok": True, "version": updater.current_version(), "url": "https://x/y.zip",
    })) is None
def test_check_none_on_error_payload():
    assert updater.check(_FakeAPI({"ok": False, "error": "network"})) is None
    assert updater.check(_FakeAPI({"ok": True, "version": "9.9.9", "url": ""})) is None
    assert updater.check(_FakeAPI(None)) is None
def test_check_swallows_api_exception():
    class Boom:
        def latest_release(self):
            raise RuntimeError("boom")
    assert updater.check(Boom()) is None

def test_check_rejects_a_payload_with_no_sha256():
    """The release sha256 (the hash the binary is installed and relaunched with) is
    now required. Without it the payload is treated as unreadable — "nothing newer"
    — so a row that arrived from a newer build (or a dropped field) is not handed to
    a pill that asks the player to install an unhashed binary.
    """
    for variant in (
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET},
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
         "sha256": None},
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
         "sha256": ""},
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
         "sha256": "   "},
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
         "sha256": "0", "size": None},
    ):
        got = updater.check(_FakeAPI(variant))
        assert got is None, (variant, got)


def test_check_rejects_a_payload_with_code_sha256():
    """A non-string sha256 (e.g. the payload hands back an integer or a list) is
    not accepted either — only a string hash can be verified and installed.
    """
    for variant in (
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
         "sha256": 7, "size": None},
        {"ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
         "sha256": [], "size": None},
    ):
        got = updater.check(_FakeAPI(variant))
        assert got is None, (variant, got)


def test_check_rejects_a_payload_with_no_size():
    """A payload missing its size is still installed, but only after the sha256
    check passes. size is optional for the progress bar, mandatory for the hash.
    """
    got = updater.check(_FakeAPI({
        "ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
        "sha256": "abc", "size": None,
    }))
    assert got is None, got
    got = updater.check(_FakeAPI({
        "ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
        "sha256": "abc", "size": 10,
    }))
    assert isinstance(got, updater.UpdateInfo), got


def test_check_refuses_a_payload_whose_version_is_not_a_version():
    """The oracle is the internet, and the pill PRINTS what it hands over.

    `parse_version` drops non-digits BY DESIGN, so "latest" and
    "9.9.9; DROP TABLE" both compared as newer and reached the sidebar pill,
    which would have read "Update to vlatest" while asking the player to install
    it. A version the app cannot show never gets past the wire now.
    """
    for junk in ("latest", "9.9.9; DROP TABLE", "999!", "  ", "", None, "v",
                 "9.9.9 (latest)", "<b>9.9.9</b>"):
        assert updater.check(_FakeAPI({
            "ok": True, "version": junk, "url": "https://x/y.zip",
        })) is None, junk
def test_check_drops_a_release_url_the_app_would_not_fetch():
    """The payload's `url` is fetched by the downloader, so it must be an URL.

    A scheme-less string or a non-http scheme is a browser error page or a
    local-file launch, not a download: the check reports "nothing newer" rather
    than handing that to urllib.
    """
    for junk in ("not a url", "file:///C:/x.exe", "javascript:alert(1)",
                 "ftp://x/y.zip", "https://", "", None):
        assert updater.check(_FakeAPI({
            "ok": True, "version": "999.0.0", "url": junk,
        })) is None, junk
def test_an_asset_url_from_a_host_we_do_not_publish_on_is_refused():
    """The payload's `url` is not merely fetched: what comes back is written
    over the installed exe and relaunched (`download_and_stage` ->
    `apply_and_relaunch`).

    `http_url` accepts ANY host with a scheme, so the release JSON could name
    `https://evil.example/x.exe` and the updater would have downloaded that
    binary, staged it as `<exe>.new` next to the running app and swapped it in on
    the next confirm - the oracle's response was the whole of the trust. The
    asset now has to live on a host FareverPal actually publishes on: GitHub, or
    the user-content CDN GitHub hands a release asset to.
    """
    for url in ("https://github.com/kohan-mucan/FareverPal/releases/download/"
                "v9.9.9/FareverPal.exe",
                "https://objects.githubusercontent.com/x/y.exe",
                "https://release-assets.githubusercontent.com/x/y.exe",
                "https://github-releases.githubusercontent.com/x/y.exe",
                "https://GitHub.com/x/y.exe",        # the host match is case-blind
                # the HOST is what is trusted, not the text of the URL: a path
                # that happens to spell another domain is still github.com
                "https://github.com/x/y.zip.evil.example"):
        info = updater.check(_FakeAPI({"ok": True, "version": "999.0.0",
                                       "url": url}))
        assert info is not None, url
        assert info.url == url, url

    for url in ("https://evil.example/x.exe",
                # the oracle is where this URL CAME FROM: a release row that
                # points back at the oracle is a row serving its own binary
                "https://farever-pals.com/x.exe",
                "http://github.com.evil.example/x.exe",     # suffix, wrong side
                "https://evil-githubusercontent.com/x.exe",  # not a real subdomain
                "https://notgithub.com/x.exe",
                "https://xgithub.com/x.exe",
                "ftp://github.com/x.exe"):
        assert updater.check(_FakeAPI({"ok": True, "version": "999.0.0",
                                       "url": url})) is None, url


def test_the_download_path_refuses_a_host_we_do_not_publish_on(tmp_path,
                                                               monkeypatch):
    """The gate is enforced where the download HAPPENS, not only at `check`.

    `check` refuses the payload, but `download_and_stage` is the function that
    writes a binary over the installed exe, and it takes an `UpdateInfo` - so a
    future caller, a hand-rolled one, or a payload that got past an older build
    must not be able to make it fetch from an arbitrary host. The refusal is
    raised BEFORE the network (nothing is requested, no temp file appears) and
    the installed exe is left byte-for-byte alone.
    """
    exe = tmp_path / "FareverPal.exe"
    exe.write_bytes(b"MZ" + b"PADDING!" * 4)
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    monkeypatch.setattr(updater, "exe_path", lambda: exe)

    calls = []

    class _ReachedNetwork(Exception):
        pass

    def _urlopen(req, *a, **k):
        calls.append(req.full_url)
        raise _ReachedNetwork(req.full_url)

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)

    bad = UpdateInfo(version="9.9.9", url="https://evil.example/x.exe")
    with pytest.raises(RuntimeError) as caught:
        updater.download_and_stage(bad)
    assert "not on a trusted host" in str(caught.value)
    assert "evil.example" in str(caught.value)      # names what was refused
    assert calls == [], calls                       # nothing was fetched
    assert not (tmp_path / "FareverPal.exe.download").exists()
    assert not (tmp_path / "FareverPal.exe.new").exists()
    assert exe.read_bytes() == b"MZ" + b"PADDING!" * 4

    # and the same call from a trusted host DOES reach the network, so the
    # refusal above is the trust list and not a download path that never works
    ok = UpdateInfo(version="9.9.9", url=_TRUSTED_ASSET)
    with pytest.raises(_ReachedNetwork):
        updater.download_and_stage(ok)
    assert calls == [_TRUSTED_ASSET], calls


def test_a_release_page_that_is_not_a_page_is_left_empty():
    """`html_url` feeds the browser fallback, so junk is dropped at the wire.

    Left EMPTY rather than swapped for the fallback: `page_url` is the one place
    that decides where a click goes, and two of them would eventually disagree.
    """
    info = updater.check(_FakeAPI({
        "ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
        "html_url": "javascript:alert(1)",
    }))
    assert info is not None and info.html_url == ""
    assert updater.page_url(info.html_url) == updater.DOWNLOAD_PAGE
    good = updater.check(_FakeAPI({
        "ok": True, "version": "999.0.0", "url": _TRUSTED_ASSET,
        "html_url": "https://github.com/x/y/releases/tag/v999.0.0",
    }))
    assert updater.page_url(good.html_url).endswith("v999.0.0")
def test_clean_version_is_the_only_version_the_ui_may_print():
    """What the pill may draw: a version, once, without the payload's own `v`.

    Every surface that shows a version adds its own "v", so `v9.9.9` from the
    oracle painted "Update to vv9.9.9".
    """
    assert updater.clean_version("v9.9.9") == "9.9.9"
    assert updater.clean_version("V0.3.7-rc1") == "0.3.7-rc1"
    assert updater.clean_version(" 9.9.9 ") == "9.9.9"
    assert updater.clean_version("0.3.7+build") == "0.3.7+build"
    for junk in ("", None, "latest", "v", "9.9.9 (latest)", "<b>9.9.9</b>"):
        assert updater.clean_version(junk) == "", junk
def test_the_startup_check_raises_the_update_pill(cp, monkeypatch):
    """`_on_update_found` is wired, not merely defined: the startup check runs
    a newer release through it and the sidebar pill shows. The dead-code sweep
    flagged this round trip as missing (the pill's only handler had no caller),
    so it is pinned here.
    """
    info = UpdateInfo(version="9.9.9", url="https://x/y.zip", notes="n")
    monkeypatch.setattr(updater, "check", lambda *a, **k: info)
    assert cp._update_btn.isHidden() is True

    cp._start_update_check()

    for _ in range(300):        # the worker hands the result back on this thread
        QtWidgets.QApplication.processEvents()
        if not cp._update_btn.isHidden():
            break
        QtCore.QThread.msleep(10)
    assert cp._update_info is info
    assert cp._update_btn.isHidden() is False
    assert "9.9.9" in cp._update_btn.text()
def test_a_current_install_leaves_the_update_pill_hidden(cp, monkeypatch):
    """The other half: when the oracle has nothing newer the pill stays down,
    and a source run never polls at all (`ControlPanel` starts the check for
    the packaged exe only, so the suite never touches the network - asserted
    here so a future unconditional start is a red test, not a live request).
    """
    assert cp._update_check_worker is None          # unfrozen: no startup poll
    monkeypatch.setattr(updater, "check", lambda *a, **k: None)

    cp._start_update_check()
    for _ in range(50):
        QtWidgets.QApplication.processEvents()
        QtCore.QThread.msleep(5)
    assert cp._update_info is None
    assert cp._update_btn.isHidden() is True
def test_the_pill_refuses_a_release_it_cannot_name(cp):
    """The sidebar button must never carry a label it cannot stand behind.

    The last gate is the widget's own, below `check`: a caller handing over a
    release with no displayable version leaves the button hidden and records NO
    release, so the click handler cannot act on something nobody can name.
    "Update to v" over a live sidebar is exactly the misleading label this
    prevents.
    """
    for junk in ("", "   ", "latest", "v", None):
        cp._on_update_found(UpdateInfo(version=junk, url="https://x/y.zip"))
        assert cp._update_info is None, junk
        assert cp._update_btn.isHidden() is True, junk
        assert cp._update_btn.text() == "", junk
def test_the_pill_label_is_the_version_and_only_the_version(cp):
    """One `v`, never doubled, never truncated - and the retry label obeys the
    same rule, because it is built by the same function."""
    assert update_pill_text(UpdateInfo(version="v9.9.9", url="u")) == "↑  Update to v9.9.9"
    assert update_pill_text(UpdateInfo(version="9.9.9", url="u"),
                            " (retry)") == "↑  Update to v9.9.9 (retry)"
    assert update_pill_text(UpdateInfo(version="junk", url="u")) == ""
    assert update_pill_text(None) == ""
    cp._on_update_found(UpdateInfo(version="v9.9.9", url="https://x/y.zip"))
    assert cp._update_btn.text() == "↑  Update to v9.9.9"
    assert cp._update_btn.isHidden() is False
    assert cp._update_info.version == "v9.9.9"   # the caller's object is not rewritten
def test_the_pill_opens_the_release_page_or_the_fallback_but_never_junk(cp, monkeypatch):
    """A source run hands the click to the BROWSER, so it must get a page.

    `html_url` comes off the wire: a `javascript:` string there used to be passed
    to webbrowser.open as-is - a local-file launch dressed as an update button.
    """
    import webbrowser
    opened = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(updater, "is_frozen", lambda: False)

    cp._on_update_found(UpdateInfo(version="9.9.9", url="https://x/y.zip",
                                   html_url="javascript:alert(1)"))
    cp._on_update_clicked()
    assert opened == [updater.DOWNLOAD_PAGE]

    cp._on_update_found(UpdateInfo(version="9.9.9", url="https://x/y.zip",
                                   html_url="https://github.com/x/y/releases"))
    cp._on_update_clicked()
    assert opened[-1] == "https://github.com/x/y/releases"
def test_the_update_check_default_matches_the_updater():
    """The mode spelled in `Settings` and the updater's own default are one value.

    The config layer cannot import core at module import time (a frozen-app
    import cycle), so the default literal is written twice on purpose - and
    pinned here, because a drift would have the Dev picker show one mode while
    the startup gate applied another.
    """
    assert Settings().check_updates == updater.DEFAULT_UPDATE_CHECK
    assert Settings().check_updates in updater.UPDATE_CHECK_MODES
    assert len(set(updater.UPDATE_CHECK_MODES)) == 3


def test_a_stored_boolean_update_check_loads_into_the_mode_it_meant():
    """`check_updates` was a BOOLEAN; every reader has to go through the mode now.

    True meant "poll this source run too" and False "only where an update can be
    installed", so a settings.json written before the change still has to load
    into the mode it MEANT - and anything unrecognisable has to land on the
    conservative default rather than being guessed at.
    """
    for stored, mode in ((True, "Always"), (False, "Packaged only"),
                         ("Always", "Always"), ("never", "Never"),
                         ("Never", "Never"), ("packaged only", "Packaged only"),
                         ("packaged-only", "Packaged only"), ("packaged", "Packaged only"),
                         ("frozen", "Packaged only"), ("off", "Never"),
                         (None, "Packaged only"), ("", "Packaged only"),
                         ("  always  ", "Always"), ("alwyas", "Packaged only"),
                         (7, "Packaged only"), ([], "Packaged only")):
        got = updater.normalize_update_check(stored)
        assert got == mode, (stored, got)
        assert got in updater.UPDATE_CHECK_MODES, stored


def test_the_update_check_mode_decides_whether_this_run_polls():
    """The matrix: only `Always` polls a source run, only `Never` stops a build."""
    assert updater.should_check("Always", frozen=False) is True
    assert updater.should_check("Always", frozen=True) is True
    assert updater.should_check("Packaged only", frozen=True) is True
    assert updater.should_check("Packaged only", frozen=False) is False
    assert updater.should_check("Never", frozen=True) is False
    assert updater.should_check("Never", frozen=False) is False
    # An unset / unreadable value is the DEFAULT MODE, which is the conservative
    # end for a source run - and unchanged behaviour for a packaged build (the
    # packaged exe has always polled, junk settings file or not).
    assert updater.should_check(None, frozen=False) is False
    assert updater.should_check("", frozen=False) is False
    assert updater.should_check("", frozen=True) is True
    assert updater.should_check(True, frozen=False) is True   # the old boolean
    assert updater.should_check(False, frozen=True) is True


def test_the_update_setting_opens_the_source_run_check(monkeypatch, tmp_path):
    """A SOURCE run polls only in `Always` — the mode that lets a dev see the
    update pill without a build. `updater.check` is stubbed BEFORE the panel is
    built, so this never reaches the network, and the DEFAULT (packaged only)
    does not poll a source run: that is what keeps the suite offline."""
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    monkeypatch.setattr(updater, "check", lambda *a, **k: None)
    assert Settings().check_updates == "Packaged only"   # the suite stays offline
    panel = ControlPanel(Settings(check_updates="Packaged only"))
    try:
        assert panel._update_check_worker is None    # a source run does not poll
    finally:
        destroy_panel(panel)
    panel = ControlPanel(Settings(check_updates="Always"))
    try:
        assert panel._update_check_worker is not None   # the startup poll ran
    finally:
        destroy_panel(panel)


def test_a_packaged_build_can_opt_out_of_the_update_check(monkeypatch, tmp_path):
    """`Never` is the opt-out a frozen build never had.

    The packaged exe always polled - there was no switch at all, short of a
    hosts-file entry. `Never` has to win even against `is_frozen()`, which is
    the case only a mode can express; `Packaged only` is the same build
    behaving as it always did.
    """
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    monkeypatch.setattr(updater, "check", lambda *a, **k: None)
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    panel = ControlPanel(Settings(check_updates="Never"))
    try:
        assert panel._update_check_worker is None, "a frozen build ignored Never"
    finally:
        destroy_panel(panel)
    panel = ControlPanel(Settings(check_updates="Packaged only"))
    try:
        assert panel._update_check_worker is not None   # the default still polls
    finally:
        destroy_panel(panel)
def _write_zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
def test_stage_bare_exe(tmp_path):
    # The current release convention: a bare PE exe (starts with "MZ").
    dl = tmp_path / "FareverPal.exe.download"
    dl.write_bytes(b"MZ" + b"\x00" * 64)
    dest = tmp_path / "FareverPal.exe.new"
    updater._stage_payload(dl, "FareverPal.exe", dest)
    assert dest.read_bytes().startswith(b"MZ")
    assert not dl.exists()                         # bare exe is moved, not copied
def test_stage_zip_prefers_named(tmp_path):
    dl = tmp_path / "asset.download"
    _write_zip(dl, {"README.txt": "hi", "FareverPal.exe": b"MZ-named"})
    dest = tmp_path / "FareverPal.exe.new"
    updater._stage_payload(dl, "FareverPal.exe", dest)
    assert dest.read_bytes() == b"MZ-named"
def test_stage_zip_falls_back_to_any_exe(tmp_path):
    dl = tmp_path / "asset.download"
    _write_zip(dl, {"Renamed.exe": b"MZ2"})
    dest = tmp_path / "out.new"
    updater._stage_payload(dl, "FareverPal.exe", dest)
    assert dest.read_bytes() == b"MZ2"
def test_stage_zip_raises_when_no_exe(tmp_path):
    dl = tmp_path / "asset.download"
    _write_zip(dl, {"data.bin": b"x"})
    try:
        updater._stage_payload(dl, "FareverPal.exe", tmp_path / "o.new")
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
def test_stage_rejects_unknown_payload(tmp_path):
    dl = tmp_path / "asset.download"
    dl.write_bytes(b"not an exe or zip")
    try:
        updater._stage_payload(dl, "FareverPal.exe", tmp_path / "o.new")
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
@pytest.fixture()
def cp(tmp_path, monkeypatch):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    panel = ControlPanel(Settings())
    yield panel
    destroy_panel(panel)
def test_eviction_caps_built_pages_and_rebuilds(cp):
    keys = [k for k, _, _ in NAV]
    for _ in range(2):
        for k in keys:
            cp._select_nav(k)

    built = sum(1 for v in cp._pages_built.values() if v)
    assert built <= cp._PAGE_CACHE_BUDGET, f"over budget: {built}"

    # every key's stack slot holds a live widget (page or placeholder)
    order = [k for k, _, _ in NAV]
    for k in keys:
        assert cp._widget_alive(cp.stack.widget(order.index(k))), k

    # an evicted page navigates back and rebuilds
    evicted = [k for k in keys if not cp._pages_built.get(k)]
    assert evicted, "nothing was evicted"
    cp._select_nav(evicted[0])
    assert cp._pages_built.get(evicted[0])
    assert cp._widget_alive(cp.stack.widget(order.index(evicted[0])))
def test_guarded_callbacks_survive_eviction(cp):
    keys = [k for k, _, _ in NAV]
    for _ in range(2):
        for k in keys:
            cp._select_nav(k)

    # these reach into possibly-evicted pages; they must not raise.
    # (_set_unit_hidden itself walks the hasattr-guarded cross-page syncs -
    # codex refresh - so a direct _update_unit_tag call is redundant: that
    # legacy Entity-tab hook no longer exists on ControlPanel.)
    cp._select_nav("codex")
    cp.log("after eviction")
    cp._set_unit_hidden("SomeUnit", True)
    cp._set_all_units_hidden(True)
def test_minimizing_keeps_the_visible_page_and_evicts_the_rest(cp):
    """Minimizing sheds every page EXCEPT the one you are on (2026-09-26).

    It used to evict all of them, which guaranteed that the page you had open
    was the one page destroyed and rebuilt on restore — losing its scroll
    position, its selection and any state the user had set. The rest still
    go, and that is where nearly all the memory is.

    The degradation path this test originally covered is still real and is
    still checked: an evicted slot can stay current (its tree swapped for a
    placeholder), and timer callbacks that derive the current page must
    degrade to the bare nav key rather than touch deleted tab widgets.
    """
    order = [k for k, _, _ in NAV]
    cp._select_nav("codex")
    cp._select_nav("settings")
    assert cp._pages_built.get("codex") and cp._pages_built.get("settings")
    current = (cp._current_page() or "").partition(":")[0]

    cp._on_minimized()
    from tests.qt_helpers import drain_deleted
    drain_deleted()

    # the visible page is untouched, so restoring it rebuilds nothing
    assert cp._pages_built.get(current), \
        f"minimizing tore down the page you were looking at ({current})"
    assert cp._widget_alive(getattr(cp, "_settings_tabs", None)), \
        "the visible page's tab widgets were destroyed"
    # every other built page went
    for key in order:
        if key != current:
            assert not cp._pages_built.get(key), \
                f"{key} survived minimize"

    # restoring is a no-op for a page that is still built
    cp._on_restored()
    assert cp._pages_built.get(current), "restoring rebuilt the kept page"

    # …and an evicted slot parked on the current index still degrades to its
    # bare key (sub-tab state is gone) instead of raising on deleted widgets.
    for key in order:
        if key == current:
            continue
        cp.stack.setCurrentIndex(order.index(key))
        assert cp._current_page() == key, key    # ...and the sidebar rift tick survives that state (Settings' tab bar is
    # read directly there, so pin the settings slot for it).
    cp.stack.setCurrentIndex(order.index("settings"))
    cp._update_sidebar_rift()
def test_eviction_drops_page_owned_widget_attrs(cp):
    """Evicting a page must drop the panel attrs that point INTO its tree.

    The Python references outlive the C++ objects: `deleteLater` runs on the
    next event-loop pass, so a stale `cp._codex_map_widget` stayed `hasattr`
    True and every later call into it raised `RuntimeError: Internal C++
    object ... already deleted`. That is what the combat tick was hitting
    once per character change, forever, after the Codex page had been shed.
    """
    from tests.qt_helpers import drain_deleted
    cp._select_nav("codex")
    assert cp._widget_alive(getattr(cp, "_codex_map_widget", None))

    cp._evict_page("codex")
    drain_deleted()

    assert not hasattr(cp, "_codex_map_widget"), \
        "the evicted page's canvas is still referenced by the panel"
    assert not hasattr(cp, "_map_info_lbl")
    # the cross-page calls the tick/refresh path makes now degrade quietly
    cp._clear_codex_map_selection()
    cp._refresh_codex_grid()
def test_codex_map_writes_survive_a_dead_canvas(cp):
    """A codex map write against a DELETED canvas is a no-op, not a fault.

    The liveness guard is the backstop for the race `_evict_page` cannot
    close: `deleteLater` is deferred, so between the eviction request and the
    deferred delete a page widget can still be (or just have become) dead.
    """
    from tests.qt_helpers import drain_deleted
    cp._select_nav("codex")
    canvas = cp._codex_map_widget
    lbl = cp._map_info_lbl
    cp._map_container.deleteLater()
    drain_deleted()
    assert not cp._widget_alive(canvas) and not cp._widget_alive(lbl)

    # the attrs still point at the dead objects — every one of these used to
    # raise "Internal C++ object already deleted"
    assert cp._codex_map_canvas() is None
    assert cp._codex_info_label() is None
    cp._clear_codex_map_selection()
    cp._clear_zone_dungeon_pins()
    cp._plot_zone_dungeon_pins("Z1")
    cp._plot_all_dungeon_pins()
    cp._plot_soulstone_zone_pins("Z1")
    cp._plot_all_soulstone_pins()
    cp._plot_all_visible_pins()
