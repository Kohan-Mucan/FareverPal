"""Headless-Qt test helpers (offscreen platform).

drain_deleted(): run the event queue AND deliver pending deleteLater
deletions. processEvents() alone does NOT deliver DeferredDelete in this
env (verified on PySide 6.11.1) — deleteLater'd widgets stay alive and
findChildren-visible until it runs, so rebuilt rows (filter chips, card
bodies) look duplicated. Production is unaffected (the live loop delivers
every frame); this is test-harness only.

click(): deliver a real left click, for widgets that handle mousePressEvent
themselves rather than being buttons (the dummy split's target picker).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def destroy(widget) -> None:
    """Close a widget AND delete it, flushing the deferred deletion.

    `close()` alone is not teardown for a test: the C++ objects stay in the
    QApplication, so every widget the test built keeps costing the whole
    process. Measured 2026-09-21 on the 4-file settings/ui-utils/speedrun/DPS
    batch: 26,126 live widgets left behind by earlier modules made a single DPS
    overlay test take ~2 s instead of 0.025 s, which inflated the wall clock
    inside its own body and drifted its live-rate assertions (10,000 DPS read
    back as 4,217) with nothing wrong in the DPS at all. Deleting the tree
    took the same batch from 271 s / 4 failed to 46 s / all green.

    Safe on an already-deleted widget, and on None.
    """
    if widget is None:
        return
    for step in (widget.close, widget.deleteLater):
        try:
            step()
        except Exception:
            pass
    drain_deleted()


def sweep_overlay_windows() -> int:
    """Destroy every HUD overlay window still alive; returns how many.

    Overlays are parentless top-level windows, so nothing else owns them: the
    panel that spawned them cannot destroy them, and a manager's bookkeeping
    can be out of date by the time teardown runs (see destroy_panel).
    """
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        return 0
    n = 0
    for w in list(app.topLevelWidgets()):
        if type(w).__name__.endswith("Overlay"):
            destroy(w)
            n += 1
    return n


def destroy_panel(cp) -> None:
    """Destroy a ControlPanel *and* the overlay windows it opened.

    Overlays are parentless top-level windows (`OverlayWindow.__init__` passes
    None), so they outlive the panel that spawned them and `cp.close()` cannot
    reach them — a panel-only teardown still leaves dozens of HUD windows (and
    their widget trees) alive for the rest of the session.

    Swept either side of the destroy on purpose: a panel defers its
    startup-overlay restore with a 0 ms singleShot, and the drain inside
    `destroy()` fires it — so a test that never pumped the loop leaves the
    panel only to watch teardown OPEN four fresh HUD windows (4 overlays, 208
    widgets, measured 2026-09-21). The second sweep is what catches those.
    """
    mgr = getattr(cp, "overlay_mgr", None)
    sweep_overlay_windows()
    destroy(cp)
    sweep_overlay_windows()
    try:
        if mgr is not None and isinstance(getattr(mgr, "overlays", None), dict):
            mgr.overlays.clear()
    except Exception:
        pass


def click(widget, pos=None) -> None:
    """Deliver a real left click to `widget` (a press event to its handler).

    `widget.click()` is only for buttons, and `QTest.mouseClick` needs a QTest
    import per module; this is the one line a test needs to exercise a custom
    `mousePressEvent` — the split rows' target picker is the current case.
    """
    from PySide6 import QtCore, QtGui, QtWidgets

    point = QtCore.QPointF(*(pos or (5.0, 5.0)))
    ev = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonPress, point,
                           QtCore.Qt.MouseButton.LeftButton,
                           QtCore.Qt.MouseButton.LeftButton,
                           QtCore.Qt.KeyboardModifier.NoModifier)
    QtWidgets.QApplication.sendEvent(widget, ev)


def drain_deleted(process_passes: int = 4) -> None:
    """Pump the event queue, flush DeferredDelete, pump once more so the
    layouts re-settle after the deletions land."""
    from PySide6 import QtCore, QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    for _ in range(max(process_passes, 1)):
        app.processEvents()
    QtCore.QCoreApplication.sendPostedEvents(
        None, QtCore.QEvent.DeferredDelete)
    app.processEvents()
