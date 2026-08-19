"""Weapon-skills focus-bar tests (headless Qt, offscreen).

The focused skill's description always renders in full — no clamping,
no truncation. The base-attack chain's per-hit breakdown clamps to the
first two hits with a show-all toggle (see _ChainBlock); a chain of two
or fewer hits shows no toggle.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtWidgets  # noqa: E402

from farever_companion import paths  # noqa: E402
from farever_companion.data import items as idata  # noqa: E402
from farever_companion.ui.pages.items.skills_section import (  # noqa: E402
    SkillBar, _ChainBlock)


def _data_present() -> bool:
    try:
        return paths.item_drops_path().exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _data_present(),
    reason="requires bundled data (assets/data/item_drops.json)")


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    """One offscreen QApplication for the whole module (widgets need one)."""
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# windows are kept here so nothing is garbage-collected mid-test (a
# deleted parent kills the widget behind its wrapper and strays events
# then hit a half-dead eventFilter)
_KEEP: list = []


def _flush() -> None:
    """Let layout + deferred (singleShot 0) work run."""
    for _ in range(3):
        QtWidgets.QApplication.processEvents()


def test_skillbar_focus_shows_full_description():
    # a real weapon whose skills include a long description
    ws = idata.weapon_skills("Mace_Benediction")
    long_skill = next((s for s in ws
                       if len(s.get("description") or "") > 150), None)
    assert long_skill is not None, \
        "Mace_Benediction should carry a long description"
    win = QtWidgets.QWidget()
    win.setFixedSize(500, 800)
    lay = QtWidgets.QVBoxLayout(win)
    bar = SkillBar(ws, set(), on_focus_changed=lambda: None)
    lay.addWidget(bar)
    win.show()
    _flush()
    bar.focus_sid(long_skill["id"])
    _flush()
    _KEEP.append(win)
    # the full description renders verbatim — never clamped or truncated
    labels = [w for w in win.findChildren(QtWidgets.QLabel)
              if w.text() == long_skill["description"]]
    assert labels, "the focused skill's full description should render"
    # no 2-line cap: default (unbounded) maximum height
    assert labels[0].maximumHeight() > 1000


def _chain_bar():
    """A SkillBar focused on Bow_BigGame's merged base chain (4 hits)."""
    ws = idata.weapon_skills("Bow_BigGame")
    base_id = next(s["id"] for s in ws
                   if s.get("type") == "Base Attack")
    win = QtWidgets.QWidget()
    win.setFixedSize(500, 800)
    lay = QtWidgets.QVBoxLayout(win)
    bar = SkillBar(ws, set(), on_focus_changed=lambda: None)
    lay.addWidget(bar)
    win.show()
    _flush()
    bar.focus_sid(base_id)
    _flush()
    _KEEP.append(win)
    return bar, win


def test_chain_block_clamps_to_first_two_hits():
    bar, win = _chain_bar()
    blocks = win.findChildren(_ChainBlock)
    assert blocks, "the focused base chain should render a chain block"
    b = blocks[0]
    assert len(b._rows) == 4
    visible = [r for r in b._rows if r.isVisible()]
    assert len(visible) == 2, "only the first two hits visible by default"
    assert b._more.isVisible()
    assert "show all 4 hits" in b._more.text()


def test_chain_show_all_expands_and_recollapses():
    bar, win = _chain_bar()
    b = win.findChildren(_ChainBlock)[0]
    b._more.clicked.emit()
    _flush()
    assert sum(1 for r in b._rows if r.isVisible()) == 4
    assert "show fewer" in b._more.text()
    b._more.clicked.emit()
    _flush()
    assert sum(1 for r in b._rows if r.isVisible()) == 2
    assert "show all 4 hits" in b._more.text()


def test_two_hit_chain_shows_no_toggle():
    # a 2-hit chain is fully visible — no toggle ever
    b = _ChainBlock([{"name": "Swing"}, {"name": "Slam"}], None)
    win = QtWidgets.QWidget()
    win.setFixedSize(400, 400)
    lay = QtWidgets.QVBoxLayout(win)
    lay.addWidget(b)
    win.show()
    _flush()
    _KEEP.append(win)
    assert all(r.isVisible() for r in b._rows)
    assert not b._more.isVisible()
