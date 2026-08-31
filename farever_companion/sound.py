""" Tiny alert-sound helper (Windows-only, uses winsound).

A handful of short WAVs are synthesised once, kept entirely in memory, and
played without ever touching the disk — nothing is written to the PC. Each
tone is decoded in a short background thread so the UI thread never blocks
on the beep. The rift alert (and any other alert) can pick which named sound
to play via play_sound().
"""
from __future__ import annotations

import io
import struct
import math
import threading

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


def play_sound(name: str = "ding") -> None:
    """Play a named alert sound without touching the disk.

    The WAV is synthesised once and cached in memory. Playback is synchronous
    from that in-memory buffer on a short background thread, so it is both
    fully in-memory (nothing written to the PC) and non-blocking for the UI."""
    try:
        import winsound
    except ImportError:
        return
    global _SOUND_CACHE
    spec = _SOUND_SPECS.get(name, _SOUND_SPECS["ding"])
    cached = _SOUND_CACHE
    if cached is None or cached[0] != name:
        data = _make_sound_wav_bytes(spec)
        _SOUND_CACHE = (name, data)
    else:
        data = cached[1]

    def _play() -> None:
        try:
            winsound.PlaySound(data, winsound.SND_MEMORY)
        except Exception:
            pass

    try:
        threading.Thread(target=_play, daemon=True).start()
    except Exception:
        # If threading somehow fails, fall back to a direct (blocking) play.
        _play()


def play_ding() -> None:
    """Backwards-compatible alias for the original two-tone alert."""
    play_sound("ding")

