"""Re-entrancy tripwire: name the cycle that overflows the C stack.

Why this exists
---------------
A stack overflow in the Qt event loop leaves almost nothing behind. The
2026-09-28 21:02 session logged exactly this::

    Current thread ...:
      File ".../farever_companion/app.py", line 390 in main     <- return app.exec()
    Current thread's C stack trace:
      <cannot get C stack on this system>

One Python frame, and no C stack to walk. The recursion is below the
interpreter — a widget event delivered while that same event is still being
delivered, a style polish re-entered by its own layout, a tree destroyed
under itself — so CPython's recursion counter never moves, no
``RecursionError`` is raised, and faulthandler has nothing to print. The
process dies with a log that names nothing.

So the diagnosis has to happen *before* the stack is gone. This module
counts nesting and, when a cycle appears, writes down what was repeating:
the widget chain, the event type, and the Python stack at that moment.

Two halves, because there are two kinds of cycle:

``install()`` — the Qt half. An application-wide event filter that tracks the
depth of *layout-shaped* events (Resize, LayoutRequest, Polish, Show,
UpdateRequest, ChildAdded). Those are the ones a cycle runs on: a widget
whose relayout provokes another relayout. Depth is per filter, not per
widget, so it counts true re-entrancy (event inside event) and not a wide
fan-out. A single deep chain is still a bug and still trips it.

``guard()`` — the Python half. A context manager for the builders that can
re-enter themselves (the collection view is built from a mode switch, a
prewarm timer and a filter toggle, and a cycle between any two of those
would spin through the event loop rather than through Python frames).
Breaks the cycle instead of riding it.

Neither is expensive and both are on: ``install()`` runs from ``app.py`` on
every start, and ``FAREVER_REENTRY`` turns it off or makes it fatal:

    (unset) / log    report and keep running — the default
    off / 0 / no     disabled
    abort            report, then exit 70 before the stack dies

A cycle deep enough to overflow the C stack will take the process with it
either way; ``abort`` just trades an unlabelled crash for a labelled one.
"""
from __future__ import annotations

import os
import traceback
from collections import defaultdict

from PySide6 import QtCore, QtWidgets

from . import crash_log

#: Events a layout cycle runs on. `ChildAdded` is in the set because a
#: builder that adds children from inside its own relayout nests the same
#: way a pure relayout does.
_WATCHED = (
    QtCore.QEvent.Type.Resize,
    QtCore.QEvent.Type.LayoutRequest,
    QtCore.QEvent.Type.Polish,
    QtCore.QEvent.Type.Show,
    QtCore.QEvent.Type.UpdateRequest,
    QtCore.QEvent.Type.ChildAdded,
)

#: Depth at which we call it a cycle. A legitimate relayout nests a handful
#: deep; a real overflow runs to the tens of thousands. 400 is far above
#: anything a healthy layout does and far below a 1 MB stack.
DEFAULT_LIMIT = 400

#: Python frames captured in the report. The deepest ones matter least —
#: the top of the stack is the widget, the bottom is the handler that
#: started it.
_REPORT_FRAMES = 40

_state: dict = {}


def _log_path() -> str:
    return os.environ.get("FAREVER_DEV_CRASH_LOG", "").strip()


def _widget_chain(w: QtCore.QObject | None, limit: int = 12) -> str:
    """`Class(objName) < Class(objName) < …` up to the root."""
    parts: list[str] = []
    seen = 0
    node = w
    while node is not None and seen < limit:
        name = node.objectName() if hasattr(node, "objectName") else ""
        parts.append(f"{type(node).__name__}({name or '-'})")
        node = node.parentWidget() if hasattr(node, "parentWidget") else None
        seen += 1
    return " < ".join(parts)


class _EventDepthFilter(QtCore.QObject):
    """Counts nesting of layout-shaped events and reports the cycle."""

    def __init__(self, limit: int, abort: bool = False, parent=None):
        super().__init__(parent)
        self._limit = limit
        self._abort = abort
        self._depth = 0
        self._per_type: dict = defaultdict(int)
        self._reported: set = set()

    def depth(self) -> int:
        return self._depth

    def reset(self) -> None:
        self._depth = 0
        self._per_type.clear()

    def eventFilter(self, obj, event):  # noqa: N802 (Qt naming)
        """Qt glue only. The depth accounting lives in _enter/_exit so it can
        be driven directly by a test: Qt POSTS a nested relayout rather than
        sending it, so a genuine nested delivery cannot be staged from
        Python, and an eventFilter that owned the counting could only ever
        be tested by a flat loop — which never nests and so never fires."""
        etype = event.type()
        if etype not in _WATCHED:
            return False
        self._enter(obj, etype)
        try:
            return False
        finally:
            self._exit(etype)

    def _enter(self, obj, etype) -> None:
        self._depth += 1
        self._per_type[etype] += 1
        if self._depth < self._limit:
            return
        key = (int(etype), id(obj))
        if key in self._reported:
            return
        self._reported.add(key)
        self._report(obj, etype)
        if self._abort:
            os._exit(70)

    def _exit(self, etype) -> None:
        self._depth -= 1
        self._per_type[etype] -= 1

    def _report(self, obj, etype) -> None:
        path = _log_path()
        if not path:
            return
        worst = max(self._per_type.items(), key=lambda kv: kv[1],
                    default=(None, 0))
        shown = ""
        if isinstance(obj, QtWidgets.QWidget):
            shown = repr(obj.text())[:200] if hasattr(obj, "text") else ""
        text = "\n".join([
            "=== re-entrancy tripwire ===",
            f"depth={self._depth} limit={self._limit}",
            f"event={etype!r} count={self._per_type.get(etype)}",
            f"deepest event={worst[0]!r} count={worst[1]}",
            f"widget chain: {_widget_chain(obj)}",
            f"widget text: {shown}",
            "--- python stack ---",
            "".join(traceback.format_stack(limit=_REPORT_FRAMES)),
        ])
        try:
            crash_log.write_python_exception(path, text + "\n",
                                             label="Re-entrancy tripwire")
        except Exception:
            pass


def install(app=None, limit: int | None = None,
            abort: bool | None = None) -> "_EventDepthFilter | None":
    """Install the Qt event-depth watchdog. Returns it, or None when it is
    switched off. `app` defaults to the running QApplication.

    ON by default. A layout or signal cycle kills the process with a log
    that names nothing, so the insurance earns its keep on every session
    rather than only on the ones somebody remembered to arm it — the cost
    is one integer compare per layout event. ``FAREVER_REENTRY``:
    ``off``/``0``/``no`` to disable, ``log`` (the default) to report and
    keep going, ``abort`` to report and exit 70 while the stack still
    exists."""
    mode = os.environ.get("FAREVER_REENTRY", "log").strip().lower()
    if mode in ("", "0", "off", "no"):
        return None
    if limit is None:
        limit = DEFAULT_LIMIT
    if abort is None:
        abort = mode in ("abort", "2", "exit")
    app = app or QtWidgets.QApplication.instance()
    if app is None:
        return None
    filt = _EventDepthFilter(int(limit), abort=bool(abort))
    app.installEventFilter(filt)
    # the filter must outlive this call: Qt holds only a pointer to it
    _state["filter"] = filt
    _state["app"] = app
    return filt


def active() -> "_EventDepthFilter | None":
    """The installed watchdog, if any (tests and diagnostics)."""
    return _state.get("filter")


class guard:
    """Depth guard for a builder that can re-enter itself.

    ``with guard("build_collection"):`` — a second entry while the first is
    still running logs the cycle and suppresses the nested call, so a
    ping-pong between a mode switch, a prewarm timer and a filter toggle
    cannot spin through the event loop.

    Usable as a decorator too (``@guard("name")``); the wrapped function's
    return value is passed through unchanged.
    """

    def __init__(self, label: str, limit: int = 3):
        self.label = label
        self.limit = limit
        self._depth = 0

    def __call__(self, fn):
        import functools

        @functools.wraps(fn)
        def wrapper(*a, **kw):
            with self:
                return fn(*a, **kw)

        wrapper.guard = self
        return wrapper

    def __enter__(self):
        self._depth += 1
        if self._depth > self.limit:
            path = _log_path()
            if path:
                try:
                    crash_log.write_python_exception(
                        path,
                        "=== re-entrancy guard tripped ===\n"
                        f"label={self.label!r} depth={self._depth} "
                        f"limit={self.limit}\n"
                        + "".join(traceback.format_stack(limit=_REPORT_FRAMES))
                        + "\n")
                except Exception:
                    pass
        return self

    def __exit__(self, *exc):
        self._depth -= 1
        return False

    @property
    def tripped(self) -> bool:
        return self._depth > self.limit
