"""Page-cache eviction (headless Qt, offscreen).

Browses every nav item, then cycles again to force LRU eviction, and verifies:
the built-page count stays within budget, evicted keys still navigate and
rebuild, and the async-callback guards (log stream, unit-chip sync) don't
touch deleted widgets.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from farever_companion.config import Settings  # noqa: E402
from farever_companion.ui.control_panel import ControlPanel, NAV  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture()
def cp(tmp_path, monkeypatch):
    monkeypatch.setenv("FAREVER_MODDATA_DIR", str(tmp_path))
    return ControlPanel(Settings())


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
    cp._friends_poll()


def test_current_page_degrades_when_active_page_evicted(cp):
    """Minimizing evicts even the ACTIVE page, yet the stack can keep an
    evicted slot current (its widget tree swapped for a placeholder). Timer
    callbacks that derive the current page (the 1s rift sidebar tick,
    page-history snapshots) must then degrade to the bare nav key instead of
    touching the page's deleted tab widgets (_codex_tabs / _settings_tabs)
    and raising on a dead C++ object."""
    order = [k for k, _, _ in NAV]
    cp._select_nav("codex")
    cp._select_nav("settings")
    assert cp._pages_built.get("codex") and cp._pages_built.get("settings")

    cp._on_minimized()                      # sheds every page, incl. the current one
    QtWidgets.QApplication.processEvents()  # drain the event queue
    QtCore.QCoreApplication.sendPostedEvents(
        None, QtCore.QEvent.DeferredDelete)  # deleteLater only runs here
    QtWidgets.QApplication.processEvents()
    assert not cp._pages_built.get("codex")
    assert not cp._widget_alive(getattr(cp, "_codex_tabs", None))
    assert not cp._widget_alive(getattr(cp, "_settings_tabs", None))

    # An evicted page parked on the current index degrades to its bare key
    # (sub-tab state is gone) instead of raising on the deleted tabs widget.
    for key in ("codex", "settings"):
        cp.stack.setCurrentIndex(order.index(key))
        assert cp._current_page() == key, key
    # ...and the sidebar rift tick survives that state (Settings' tab bar is
    # read directly there, so pin the settings slot for it).
    cp.stack.setCurrentIndex(order.index("settings"))
    cp._update_sidebar_rift()
