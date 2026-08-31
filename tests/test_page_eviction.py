"""Page-cache eviction (headless Qt, offscreen).

Browses every nav item, then cycles again to force LRU eviction, and verifies:
the built-page count stays within budget, evicted keys still navigate and
rebuild, and the async-callback guards (log stream, unit-chip sync) don't
touch deleted widgets.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets  # noqa: E402

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

    # these reach into possibly-evicted pages; they must not raise
    cp._select_nav("codex")
    cp.log("after eviction")
    cp._set_unit_hidden("SomeUnit", True)
    cp._friends_poll()
    cp._update_unit_tag()
