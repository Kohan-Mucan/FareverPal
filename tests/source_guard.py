"""Say WHICH file a source-contract failure was about.

The problem this exists for
---------------------------
A surprising number of tests here assert against files the test process does
not own: the sibling native tree (`GameFiles/Farever/hooks/`), and the built
binaries in `dps_bridge/`, which every `build_all.bat` rewrites. In a
checkout where something else is also writing - another agent on the same
worktree, an editor with autosave, a build running alongside pytest - the read
returns whatever happened to be on disk at that instant, and the failure that
comes out says nothing about where the bytes came from.

That is not hypothetical. On 2026-09-30 22:41 a concurrent writer replaced
`tests/test_native_hook_core.py` and `dps_bridge/README.md` with pre-edit
copies. The next run failed with `assert 0 >= 20` in
`test_every_generated_findex_is_distinct_and_generator_owned`, which reads
like a real regression in the manifest and was nothing of the sort.

What it does
------------
`read_text` / `read_bytes` are drop-in replacements for the `Path` methods that
record two things: the stamp of the file (mtime + size) at the moment of the
read, and the stamp it had the FIRST time this session read it. When those
disagree, the file moved under a test that is asserting about it, and the read
raises `ConcurrentRewrite` - a name that says what happened, before any
assertion can turn it into a mystery number.

Why it raises instead of skipping
---------------------------------
A skip here would be worse than the confusion it replaces: these are the
contracts that keep a hand-written findex, a stale binary or a dead flag out of
a build. Silently skipping the guard that protects them turns a loud, one-line
explanation into a quiet hole - and the suite would go green while checking
nothing. The message says what to do instead: re-run, and if it keeps firing,
something really is watching the tree.

What it deliberately does not do
--------------------------------
- It does not read the file twice and compare contents. A file written before
  the session started is a legitimate state - that is just development - and
  only a change *within* a session is the signal here.
- It does not touch `git`, and it has no opinion about whether the content is
  correct. A stable file that is wrong still fails the assertion that owns it.
- It does not guard the app's own package sources. Those are the subject under
  test, and a failure there is an ordinary failure; the point of the guard is
  provenance for files that arrive from outside this test's own tree.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

__all__ = ["ConcurrentRewrite", "read_text", "read_bytes", "stamp", "reset_session"]


class ConcurrentRewrite(AssertionError):
    """A file this test asserts about was rewritten while the test ran.

    An `AssertionError` so it fails the test the way a contract violation
    should - one failure, in the test's own report, with the file named -
    rather than as an error the reader has to decode.
    """


#: Resolved path -> the stamp it had the first time THIS SESSION read it. The
#: baseline is deliberately per-session and per-first-read: a file edited
#: between two runs is normal, and must never raise.
_SESSION_FIRST: dict[Path, tuple[int, int] | None] = {}


def _stamp(path) -> tuple[int, int] | None:
    """(mtime_ns, size), or None if the file cannot be stat'ed."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def stamp(path):
    """The guard's idea of a file's identity: its mtime and size.

    Exposed so a test can build the same provenance into its own message when
    it needs to say which BINARY it just asserted against.
    """
    return _stamp(path)


def _when(value: tuple[int, int] | None) -> str:
    if value is None:
        return "gone"
    return f"{time.strftime('%H:%M:%S', time.localtime(value[0] / 1e9))} ({value[1]} bytes)"


def _remember(resolved: Path, before: tuple[int, int] | None) -> None:
    """Record this read, and refuse it if the file moved since we first saw it."""
    first = _SESSION_FIRST.get(resolved, False)  # False = never recorded
    if first is False:
        _SESSION_FIRST[resolved] = before
        return
    if first == before:
        return
    raise ConcurrentRewrite(
        f"another process rewrote this file while this test session was "
        f"reading it:\n"
        f"  {resolved}\n"
        f"    first read by the suite  {_when(first)}\n"
        f"    now                     {_when(before)}\n"
        f"The test asserted about bytes that were not there when it started, so "
        f"this result is not about the code. Re-run the test; if it keeps firing, "
        f"something is watching and rewriting the tree (another agent, an editor "
        f"with autosave, or a build publishing into dps_bridge/).")


def _read(path, reader):
    """Read once, and prove the file did not move while we were reading it.

    The before/after pair catches a TORN read - an editor that truncates then
    rewrites, or a publisher copying over the file - which is the one case
    where the bytes returned are not even a version of the file that existed.
    """
    resolved = Path(path)
    before = _stamp(resolved)
    body = reader()
    after = _stamp(resolved)
    if before != after:
        raise ConcurrentRewrite(
            f"this file was being rewritten WHILE the test was reading it, so "
            f"the content it got is a torn read:\n"
            f"  {resolved}\n"
            f"    before  {_when(before)}\n"
            f"    after   {_when(after)}\n"
            f"Re-run the test once whatever is writing has finished.")
    _remember(resolved, before)
    return body


def read_text(path, **kwargs):
    """`Path.read_text`, but the read is recorded. See the module docstring."""
    return _read(path, lambda: Path(path).read_text(**kwargs))


def read_bytes(path):
    """`Path.read_bytes`, but the read is recorded. See the module docstring."""
    return _read(path, lambda: Path(path).read_bytes())


def reset_session() -> None:
    """Forget the first-read baseline. For tests of the guard itself."""
    _SESSION_FIRST.clear()
