""" Tiny alert-sound helper (Windows-only, uses winsound).

A handful of short WAVs are synthesised once, kept entirely in memory, and
played without ever touching the disk — nothing is written to the PC. Each
tone is decoded and played on a SINGLE background worker thread, so the UI
thread never blocks on the beep, alerts keep the order they were requested
in, and two of them can never sound at once. The rift alert (and any other alert) can pick which named sound
to play via play_sound().

Every sound in this app goes through play_sound(), so the burst guard lives
here too (see "burst guard" below) rather than in each caller: whatever calls
it in a loop, the OS is never asked to restart a tone mid-note.
"""
from __future__ import annotations

import io
import struct
import math
import queue
import threading
import time
from collections.abc import Callable


# FIFO of pending playbacks, drained by one worker thread. See `_play_async`
# for why playback is serialized rather than threaded per call.
_AUDIO_QUEUE: "queue.Queue[Callable[[], None] | None]" = queue.Queue()
_audio_worker: threading.Thread | None = None

# Named alert sounds. Each spec is a list of notes:
#   (freq, dur, delay, vol)  — a sine tone starting `delay`s in, lasting `dur`s.
_SOUND_SPECS: dict[str, list[tuple[float, float, float, float]]] = {
    # original two-tone A5 -> E6
    "ding":    [(880.0, 0.10, 0.0, 0.5), (1318.5, 0.20, 0.10, 0.5)],
    # single bright ping
    "ping":    [(1760.0, 0.18, 0.0, 0.45)],
    # ascending C5-E5-G5 arpeggio
    "chime":   [(523.25, 0.18, 0.0, 0.4), (659.25, 0.18, 0.12, 0.4),
                (783.99, 0.30, 0.24, 0.42)],
    # little 4-note fanfare
    "fanfare": [(523.25, 0.12, 0.0, 0.4), (659.25, 0.12, 0.12, 0.4),
                (783.99, 0.12, 0.24, 0.4), (1046.5, 0.36, 0.36, 0.45)],
    # wailing siren (two-tone back and forth)
    "siren":   [(600.0, 0.12, 0.0, 0.4), (900.0, 0.12, 0.12, 0.4),
                (600.0, 0.12, 0.24, 0.4), (900.0, 0.12, 0.36, 0.4)],
    # marimba run (C-E-G down)
    "marimba": [(523.25, 0.14, 0.0, 0.4), (659.25, 0.14, 0.14, 0.35),
                (392.0, 0.30, 0.28, 0.4)],
    # robotic beep-boop
    "robot":   [(440.0, 0.08, 0.0, 0.4), (440.0, 0.08, 0.14, 0.4),
                (554.37, 0.08, 0.28, 0.4), (440.0, 0.08, 0.42, 0.4)],
    # warm bell arpeggio with overtones
    "chime2":  [(523.25, 0.5, 0.00, 0.32), (1046.5, 0.5, 0.00, 0.12),
                (659.25, 0.5, 0.12, 0.26), (1318.5, 0.5, 0.12, 0.10),
                (783.99, 0.6, 0.24, 0.28), (1568.0, 0.5, 0.24, 0.10),
                (1046.5, 0.7, 0.40, 0.3)],
}

# Display labels for the settings picker (key -> label).
SOUND_LABELS = {
    "ding": "Ding", "ping": "Ping", "chime": "Chime",
    "fanfare": "Fanfare",
    "siren": "Siren", "marimba": "Marimba",
    "robot": "Robot", "chime2": "Bell",
}

# Cache for the single sound currently in use. Kept entirely in memory (nothing
# is ever written to disk). Only ONE tone lives here at a time — if the user
# previews or switches sounds, the previous one is replaced, not accumulated.
# Windows cannot play asynchronously from an in-memory buffer (SND_MEMORY +
# SND_ASYNC raises RuntimeError), so playback runs synchronously inside a short
# background thread instead.
_SOUND_CACHE: tuple[str, bytes] | None = None


# --- burst guard -----------------------------------------------------------
#
# `play_sound` is this app's only audio entry point, and the OS gives it one
# channel per process: a second call cancels the tone already sounding and
# starts the new one. For a *different* alert that is the right behaviour
# (previewing sounds, a newer alert superseding an older one). For the SAME
# alert it is only ever a fault — nothing legitimately wants the same tone
# restarted mid-note, and a caller that fires in a loop that way sounds like
# one long stutter rather than an alert.
#
# So: the same tone is never restarted while it is still sounding, and no two
# tones start within a machine-rate gap of each other. Both are measured in
# monotonic seconds, latched per name, and reported to the Activity Log on
# the way out, so a burst that does happen names its own cause instead of
# having to be guessed at from the sound.
_BURST_GAP_S = 0.12          # a human click is ~10x slower than this
_MIN_REPEAT_S = 1.0          # no alert tone repeats faster than 1/s
_SUPPRESS_REPORT_S = 2.0     # at most one log line per burst, per name
_last_started: dict[str, float] = {}
_last_any_start = 0.0
_suppressed: dict[str, int] = {}
_last_reported: dict[str, float] = {}
_guard_lock = threading.Lock()
_logger: Callable[[str], None] | None = None


def set_sound_logger(cb: Callable[[str], None] | None) -> None:
    """Register the Activity Log sink used to report suppressed alerts.

    Kept as a callback so this module stays free of Qt (and of any knowledge
    of the UI): the panel passes its own `log` method in at construction.
    """
    global _logger
    _logger = cb


def suppressed_counts() -> dict[str, int]:
    """Per-name count of alerts the burst guard dropped (diagnostics/tests)."""
    with _guard_lock:
        return dict(_suppressed)


def reset_sound_state() -> None:
    """Forget every latch — used by tests so one case can't colour the next."""
    global _last_any_start
    with _guard_lock:
        _last_started.clear()
        _suppressed.clear()
        _last_reported.clear()
        _last_any_start = 0.0


def _now() -> float:
    """Monotonic clock (a seam: tests drive it instead of sleeping)."""
    return time.monotonic()


def _tone_seconds(notes: list[tuple[float, float, float, float]]) -> float:
    """Length of the WAV built from `notes` — same arithmetic the synth uses."""
    return max((dur + delay) for (_, dur, delay, _) in notes) + 0.05


def _log_line(msg: str) -> None:
    cb = _logger
    if cb is None:
        return
    try:
        cb(msg)
    except Exception:
        pass


def _guard(name: str, seconds: float, force: bool) -> bool:
    """True when this alert may play now; records/queues a report when not.

    `force` is for an explicit user action — the settings sound picker plays
    the tone the user just clicked, and re-hearing the same one on a second
    click is the point of it, not a fault.
    """
    global _last_any_start
    if force:
        with _guard_lock:
            _last_started[name] = _now()
            _last_any_start = _last_started[name]
        return True

    now = _now()
    # A tone shorter than a second would still let a 3-per-second caller
    # stutter, so the repeat window is the tone's own length or a second,
    # whichever is longer. Real lead times are >= 59 s apart, so no genuine
    # alert is ever inside this window.
    seconds = max(seconds, _MIN_REPEAT_S)
    report = ""
    with _guard_lock:
        started = _last_started.get(name)
        if started is not None and now - started < seconds:
            n = _suppressed[name] = _suppressed.get(name, 0) + 1
            if now - _last_reported.get(name, 0.0) >= _SUPPRESS_REPORT_S:
                _last_reported[name] = now
                report = (f"Alert sound '{name}' suppressed ×{n} this session — the "
                          f"tone is still sounding ({now - started:.2f}s into "
                          f"{seconds:.2f}s)")
        elif now - _last_any_start < _BURST_GAP_S:
            n = _suppressed[name] = _suppressed.get(name, 0) + 1
            report = (f"Alert sound '{name}' suppressed ×{n} this session — "
                      f"another alert had just started")
        else:
            _last_started[name] = now
            _last_any_start = now
            return True

    if report:
        _log_line(report)
    return False


def list_sounds() -> list[str]:
    """Available sound keys (display order = spec definition order)."""
    return list(_SOUND_SPECS.keys())


def _make_sound_wav_bytes(notes: list[tuple[float, float, float, float]]) -> bytes:
    """Synthesise a WAV entirely in memory (no temp file on disk)."""
    import wave
    sr = 44100
    total = max((d + delay) for (_, d, delay, _) in notes) + 0.05
    n = int(sr * total)
    frames = bytearray()
    for i in range(n):
        t = i / sr
        s = 0.0
        for (freq, dur, delay, vol) in notes:
            lt = t - delay
            if lt < 0.0 or lt > dur:
                continue
            env = max(0.0, 1.0 - lt / dur) ** 1.5  # quick attack, smooth decay
            s += math.sin(2 * math.pi * freq * lt) * vol * env
        iv = int(max(-1.0, min(1.0, s)) * 32767)
        frames += struct.pack("<h", iv)
    buf = io.BytesIO()
    with wave.open(buf, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _play_wav(data: bytes) -> None:
    """Hand the in-memory WAV to Windows (the only place the OS is touched).

    SND_NODEFAULT matters here: without it Windows substitutes the generic
    system ding whenever it can't play the buffer, which is exactly the
    wrong-sounding noise a botched playback would otherwise produce.
    """
    import winsound
    flags = winsound.SND_MEMORY
    nodflt = getattr(winsound, "SND_NODEFAULT", 0)
    winsound.PlaySound(data, flags | nodflt)


def _play_async(fn) -> None:
    """Hand one playback to the SINGLE audio worker (tests run it inline).

    Until 2026-10-03 this spawned a fresh thread per call, so two alerts that
    passed the burst guard milliseconds apart became two concurrent
    `winsound.PlaySound` calls. `winsound` is not thread-safe and Windows gives
    the process ONE audio channel, so the outcome was arbitrary: the pair could
    interleave, cut each other off, or land in either order. That is exactly
    how it was reported from the field — rift chimes "going off 2 at a time and
    not in right order" — and the scheduler's own tests could not see it,
    because they record `play_sound` CALLS, which were always in order.

    One FIFO drained by one worker fixes the property the symptom is about:
    `_play_wav` blocks for the tone's whole length, so a queued alert cannot
    start until the previous one has finished. Order is the order of arrival,
    and nothing overlaps.
    """
    global _audio_worker
    try:
        if _audio_worker is None or not _audio_worker.is_alive():
            _audio_worker = threading.Thread(target=_drain_audio,
                                              name="fv-audio", daemon=True)
            _audio_worker.start()
        _AUDIO_QUEUE.put(fn)
    except Exception:
        # If threading somehow fails, fall back to a direct (blocking) play.
        fn()


def _drain_audio() -> None:
    """Play queued alerts one at a time, in arrival order, forever."""
    while True:
        fn = _AUDIO_QUEUE.get()
        try:
            if fn is None:            # shutdown sentinel
                return
            fn()
        except Exception:
            pass
        finally:
            _AUDIO_QUEUE.task_done()


def play_sound(name: str = "ding", *, force: bool = False) -> None:
    """Play a named alert sound without touching the disk.

    The WAV is synthesised once and cached in memory. Playback is synchronous
    from that in-memory buffer on a short background thread, so it is both
    fully in-memory (nothing written to the PC) and non-blocking for the UI.

    A repeat of the same tone inside its own length — or a second, for a short
    one — is dropped (and reported) rather than restarted mid-note, so no
    caller firing in a loop can be heard as anything but a single alert. Pass
    `force=True` only for an explicit user action such as previewing a sound
    they just clicked.
    """
    try:
        import winsound  # noqa: F401  (availability probe)
    except ImportError:
        return
    global _SOUND_CACHE
    spec = _SOUND_SPECS.get(name, _SOUND_SPECS["ding"])
    if not _guard(name, _tone_seconds(spec), force):
        return

    cached = _SOUND_CACHE
    if cached is None or cached[0] != name:
        data = _make_sound_wav_bytes(spec)
        _SOUND_CACHE = (name, data)
    else:
        data = cached[1]

    def _play() -> None:
        try:
            _play_wav(data)
        except Exception:
            pass

    _play_async(_play)


def play_ding() -> None:
    """Backwards-compatible alias for the original two-tone alert."""
    play_sound("ding")


if __name__ == "__main__":  # manual smoke test: python -m farever_companion.runtime.sound
    for _name in list_sounds():
        print(f"playing {_name}…")
        play_sound(_name, force=True)
        time.sleep(_tone_seconds(_SOUND_SPECS[_name]) + 0.1)

