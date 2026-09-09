"""The developer crash log (``FAREVER_DEV_CRASH_LOG``): one writer, one clock.

``run-log.bat`` points that env var at ``run.log`` and ``run.py`` arms
``faulthandler`` with it, so a developer run leaves a stack dump behind when the
process dies on an access violation. Two things made that file hard to read, and
both are fixed here.

**Five writers became one.** faulthandler held a file object, ``run.py`` and
``app.py`` each opened the path for a Python traceback, ``write_session_marker``
opened it once more, and the pruning vectored exception handler had a descriptor
of its own. Those writers disagreed about line endings — the text-mode ones
translated ``\\n`` to ``\\r\\n`` while faulthandler and the raw ``WriteFile`` did
not — so the 2026-09-25 run.log mixed ``\\r\\n`` session markers in among ``\\n``
dumps with no way to tell where an entry began. Every entry now goes through ONE
``_DevLog`` descriptor and ONE lock, with one line ending, so entries land in the
order they happened.

**The stamp's clock was frozen at install.** The line above a fatal dump was
built once, when the handler was installed, so it reported the SESSION START
rather than the crash — the 2026-09-25 run.log shows the stamp and the session
marker carrying the same second (``=== fatal exception 14:22:28 ===`` directly
beside ``=== session start 14:22:28 pid=8636 ===``). ``refresh_stamp_payload``
rebuilds that line every second from a normal daemon thread instead, so the
stamp is the crash's own clock while the fault path still only ever does one raw
``WriteFile`` of a pre-allocated buffer: no allocation, no file object, no GIL,
on a heap that may be exactly what just broke.

The stamp comes from a vectored exception handler installed right after
``faulthandler.enable()``: VEHs run before the unhandled-exception path and are
called in REVERSE registration order, so registering second puts our line above
``faulthandler``'s dump. It returns ``EXCEPTION_CONTINUE_SEARCH`` so the dump is
still written underneath it.

Windows only (that is all the app ships for); everywhere else every entry point
here is a quiet no-op. Nothing here is ever imported by a normal user run: the
caller gates on the env var the dev launcher sets.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from datetime import datetime

#: Exception codes worth a stamp. A Windows process raises MANY first-chance
#: exceptions that are not crashes (C++ throws, the .NET/COM runtime, an
#: access violation caught by a ctypes `__try`), and a VEH sees every one of
#: them: stamping those would fill run.log from a healthy process. So this is
#: the fatal set faulthandler dumps for — minus one:
#: EXCEPTION_STACK_OVERFLOW (0xC00000FD) is deliberately NOT stamped. A vectored
#: handler for it runs on the very stack that just overflowed, so the write can
#: fault again and take faulthandler's dump down with it. That entry is bounded
#: by the session marker instead of stamped.
STAMPED_EXCEPTION_CODES = frozenset({
    0xC0000005,     # EXCEPTION_ACCESS_VIOLATION
    0xC0000006,     # EXCEPTION_IN_PAGE_ERROR
    0xC000001D,     # EXCEPTION_ILLEGAL_INSTRUCTION
    0xC0000094,     # EXCEPTION_INT_DIVIDE_BY_ZERO
    0xC0000096,     # EXCEPTION_PRIV_INSTRUCTION
})

_STAMP_FMT = "%Y-%m-%d %H:%M:%S"

#: Install state: the ctypes callback MUST stay referenced for the life of the
#: process — if it is collected, the handler Windows still holds a pointer to
#: becomes a dangling one and the next exception crashes inside the VEH.
_registered: list = []

#: The pre-built fatal stamp and its fixed length. The fault handler only READS
#: these (one raw WriteFile, no allocation); `refresh_stamp_payload` rewrites the
#: buffer in place from an ordinary thread, so the line a crash leaves behind
#: carries the crash's clock instead of the session's start time.
_stamp_buffer = None
_stamp_len = 0
_stamp_clock: threading.Thread | None = None

#: The rolling activity tail. ``set_activity_tail`` (re)builds a fixed-shape
#: byte buffer from the caller's most recent log lines; the same 1 Hz clock
#: that refreshes the fatal stamp rewrites it in place, so the lines a crash
#: lands next to are at most one second stale — and the fault path itself
#: still only ever does one raw WriteFile of a pre-allocated buffer (no
#: allocation, no GIL) for BOTH blocks. Without this, a hard crash leaves the
#: dumps but nothing about what the app was doing: the Activity Log lives
#: only in RAM, and the 2026-09-28 21:17–21:32 stack overflows each died
#: beside a dump that names nothing but the parking spot of two daemon
#: threads.
_activity_buffer = None
_activity_len = 0

#: One writer per log path. The app has exactly one; tests open several.
_logs: dict[str, "_DevLog"] = {}


def stamp_line(now: datetime | None = None) -> str:
    """The one line written above a fatal dump (ends with a newline).

    The pid is in the header, not just on the session-start line, because a
    dump has to be attributable to the run that produced it. It was not: with
    only a timestamp, the owning session could be recovered by inferring it
    from the surrounding `session start` markers, and that inference is wrong
    the moment the file interleaves two runs — the 2026-10-03 12:37 dump reads
    as belonging to the session that started at 12:40, three minutes AFTER it.

    Zero-padded to a fixed width on purpose. This string is pre-allocated once
    into a fixed-size buffer that `refresh_stamp_payload` rewrites in place,
    and it keeps only the same-length payload; a pid whose digit count changed
    would silently fail that check and freeze the stamp at its startup text.
    """
    ts = (now or datetime.now()).strftime(_STAMP_FMT)
    return f"\n=== fatal exception {ts} pid={os.getpid():08d} ===\n"


def activity_tail_block(lines: list[str], max_chars: int = 2000) -> str:
    """The activity block written under a fatal stamp (ends with a newline).

    ``lines`` is the caller's log, oldest first — the block keeps the LAST
    ones that fit `max_chars` (whole lines; a line that does not fit drops
    everything before it, never the middle). Pure so the caller can shape
    and test it without a live handler."""
    keep: list[str] = []
    used = 0
    for ln in reversed(lines or []):
        n = len(ln.encode("utf-8", errors="replace")) + 1
        if used + n > max_chars:
            break
        keep.append(ln)
        used += n
    body = "\n".join(reversed(keep))
    return f"\n=== activity tail ===\n{body}\n" if keep else ""


def set_activity_tail(lines: list[str]) -> None:
    """Rebuild the pre-allocated activity buffer from the caller's log.

    Called from the GUI thread (the log owner) whenever its tail changes —
    NOT from the fault path, which only reads the buffer. A fixed-shape
    bytes buffer of the block's exact length is kept so the fault handler
    writes without allocating; a block that shrank leaves the old length's
    tail padded away by writing only the live length."""
    global _activity_buffer, _activity_len
    payload = activity_tail_block(lines).encode("utf-8")
    _activity_buffer = payload
    _activity_len = len(payload)


def session_line(now: datetime | None = None) -> str:
    """The line written once per dev run, so appends are attributable.

    ``run.log`` is appended by every session (``run-log.bat`` even loops the
    app), and a dump that never reached the VEH — a hard kill, a hang, power
    loss — still leaves a boundary behind.
    """
    ts = (now or datetime.now()).strftime(_STAMP_FMT)
    return f"\n=== session start {ts} pid={os.getpid()} ===\n"


def python_exception_line(now: datetime | None = None,
                          label: str = "Python exception") -> str:
    """The header above an uncaught Python traceback (ends with a newline)."""
    ts = (now or datetime.now()).strftime(_STAMP_FMT)
    return f"\n=== {label} {ts} ===\n"


def refresh_stamp_payload(now: datetime | None = None) -> bytes:
    """Rebuild the fatal stamp in place. NEVER called from the fault path.

    Called by the 1 Hz clock thread below. Rebuilding allocates, which is fine
    on a healthy thread and forbidden on a faulting one — that split is the
    whole point: the handler keeps writing a pre-allocated buffer with a single
    raw ``WriteFile``, but the buffer it writes is no longer frozen at startup.
    """
    payload = stamp_line(now).encode("utf-8")
    buf = _stamp_buffer
    if buf is not None:
        try:
            buf.raw = payload          # same fixed shape, written in place
        except (ValueError, TypeError):
            pass                       # unexpected length: keep the old stamp
    return payload


def _start_stamp_clock(interval_s: float = 1.0) -> None:
    """Keep the pre-built stamp within a second of the wall clock (idempotent).

    The buffer is rewritten in place, so a crash cannot catch a half-built
    value: the handler always writes the fixed-length buffer, and the thread
    only ever assigns a complete payload to it.
    """
    global _stamp_clock
    if _stamp_clock is not None:
        return

    def _loop() -> None:
        while True:
            time.sleep(interval_s)
            try:
                refresh_stamp_payload()
            except Exception:
                pass        # a stale stamp beats no app

    _stamp_clock = threading.Thread(target=_loop, name="crash-stamp-clock",
                                    daemon=True)
    _stamp_clock.start()


class _DevLog:
    """The ONE append path for the developer crash log.

    Every entry — session marker, Python traceback, the VEH's fatal stamp and
    faulthandler's dump — writes through this descriptor, so the file reads in
    the order things happened. ``append`` is the locked path for Python-side
    text; the fault path cannot take a lock, so it uses ``handle()`` with a raw
    ``WriteFile``. Both are safe to interleave because the descriptor is opened
    ``O_APPEND`` (verified 2026-09-25: a raw ``WriteFile`` on such a handle
    lands at EOF — the CRT opens it ``FILE_APPEND_DATA`` — so it can never
    overwrite the head of the file).

    Opened ``O_BINARY`` on purpose: a text-mode descriptor translates ``\\n`` to
    ``\\r\\n``, which is exactly how the log ended up with three different line
    ending behaviours between its five writers.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
        self._fd = os.open(path, flags, 0o644)
        # Unbuffered handle over the SAME descriptor, for faulthandler (it
        # writes to the fd, and keeps this object alive so the fd stays open).
        # Never closed: it must outlive every crash.
        self._file = os.fdopen(self._fd, "ab", 0)

    def file(self):
        """The stream to hand ``faulthandler.enable(file=...)``."""
        return self._file

    def handle(self):
        """The raw Win32 handle for the fault-time ``WriteFile``."""
        import msvcrt
        return msvcrt.get_osfhandle(self._fd)

    def append(self, text: str) -> bool:
        """Append one complete entry, locked against other Python writers.

        Written in one ``os.write`` so two threads can never interleave halves
        of an entry (the lock covers this process; ``O_APPEND`` makes the write
        itself land at EOF for any other process writing the same file).
        """
        data = text.encode("utf-8", errors="replace")
        with self._lock:
            try:
                written = 0
                while written < len(data):
                    n = os.write(self._fd, data[written:])
                    if not n:
                        return False
                    written += n
                return True
            except OSError:
                return False


def _log_for(path: str) -> "_DevLog | None":
    """The shared writer for `path`, or None when it cannot be opened."""
    try:
        key = os.path.abspath(path)
    except Exception:
        return None
    log = _logs.get(key)
    if log is None:
        try:
            log = _DevLog(key)
        except OSError:
            return None
        _logs[key] = log
    return log


def open_dev_log(path: str) -> "_DevLog":
    """The single append path for the dev crash log (raises when unopenable).

    Returns the writer shared with the VEH and faulthandler, so the caller only
    has to hand ``log.file()`` to ``faulthandler.enable`` — one descriptor for
    the whole session.
    """
    log = _log_for(path)
    if log is None:
        raise OSError(f"cannot open dev crash log: {path!r}")
    return log


def write_session_marker(path: str) -> bool:
    """Append the per-session marker. False when the file cannot be written."""
    log = _log_for(path)
    return bool(log and log.append(session_line()))


def write_python_exception(path: str, text: str,
                           label: str = "Python exception") -> bool:
    """Append an uncaught traceback through the same writer as everything else."""
    log = _log_for(path)
    if log is None:
        return False
    body = python_exception_line(label=label) + text
    if not body.endswith("\n"):
        body += "\n"
    return log.append(body)


def install_fatal_stamp(path: str):
    """Stamp fatal dumps in `path`. Returns the handler, or None when unimplemented.

    MUST be called AFTER ``faulthandler.enable()``: the two handlers are both
    vectored and the last one registered runs first.
    """
    if sys.platform != "win32" or _registered:
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None
    log = _log_for(path)
    if log is None:
        return None

    # Everything the handler needs is built ONCE, here: a fault may happen on a
    # heap that is already broken, and the handler must not allocate.
    global _stamp_buffer, _stamp_len
    payload = stamp_line().encode("utf-8")
    _stamp_buffer = ctypes.create_string_buffer(payload, len(payload))
    _stamp_len = len(payload)
    handle = wintypes.HANDLE(log.handle())
    written = wintypes.DWORD(0)
    written_ref = ctypes.byref(written)

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        write_file = k32.WriteFile
        write_file.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
        write_file.restype = wintypes.BOOL
        add_veh = k32.AddVectoredExceptionHandler
        add_veh.restype = ctypes.c_void_p
    except Exception:
        return None

    class _ExceptionPointers(ctypes.Structure):
        _fields_ = [("ExceptionRecord", ctypes.c_void_p),
                    ("ContextRecord", ctypes.c_void_p)]

    def _handler(info):
        """One stamped line, then let the chain (faulthandler) dump."""
        try:
            code = ctypes.cast(info.contents.ExceptionRecord,
                               ctypes.POINTER(wintypes.DWORD)).contents.value
            if code in STAMPED_EXCEPTION_CODES and _stamp_buffer is not None:
                # Reads only: the clock thread rebuilt this buffer in place.
                write_file(handle, _stamp_buffer, _stamp_len, written_ref, None)
                act = _activity_buffer
                act_len = _activity_len
                if act is not None and act_len:
                    write_file(handle, act, act_len, written_ref, None)
        except Exception:
            pass        # never raise out of a fault handler
        return 0        # EXCEPTION_CONTINUE_SEARCH: keep faulthandler's dump

    callback = ctypes.WINFUNCTYPE(ctypes.c_long,
                                 ctypes.POINTER(_ExceptionPointers))(_handler)
    try:
        if not add_veh(1, callback):    # 1 = first in the chain
            return None
    except Exception:
        return None
    _registered.append(callback)
    _start_stamp_clock()
    return callback
