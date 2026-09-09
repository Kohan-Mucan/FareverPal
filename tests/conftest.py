"""Test isolation — the user's live moddata folder is off limits.

Why this file exists
--------------------
`config.config_dir()` resolves to the app's real user-data folder
(`dist/moddata/`) only for a frozen release build. Every OTHER process —
pytest, dev tools, a stray import, and `run.py` itself — defaults to
`dist/dev-moddata` (see config._is_app_process, and run.py:66, which pops
`FAREVER_DATA_FLAVOR` so a source launch cannot reach the user folder). This
fixture points the whole session at a throwaway temp dir: no live folder is
reachable from a test at all, and per-test `FAREVER_MODDATA_DIR` overrides
still win.

That is not paranoia. On 2026-09-17 a fixture-less test
(`test_settings_page.py::test_settings_tabs_build_lazily` builds `_FakeHost`,
whose `__init__` creates a bare `Settings`, and the Settings page's own
`_set()` calls `save()`) wrote a defaults settings.json over the user's live
one on every suite run. The live file was left differing from fresh defaults
in exactly one key (`farever_game_dir`): every toggle the user had set was
gone, which is why settings "reset themselves" whenever the tests ran.

Kept deliberately tiny: one session-scoped autouse fixture.
"""
from __future__ import annotations

import os

import pytest

from tests import affected


# --- Affected-test scoping -------------------------------------------------
#
# `python -m pytest -q` with a dirty working tree runs only the tests the change
# can break (see tests/affected.py). CI, an explicit path/nodeid, `-k`/`-m`, or
# `--scope=all` all keep the full suite. The plan is computed once here.


def pytest_addoption(parser):
    group = parser.getgroup("farever")
    group.addoption(
        "--scope", action="store", default=None, dest="farever_scope",
        choices=("auto", "changed", "all"),
        help="Which tests to run: auto (only those a dirty working tree can "
             "affect), changed (same, forced), or all (the whole suite). CI "
             "and an explicit test path always mean all.")


# Test files with a failure/error this session, filled by the two logreport
# hooks below and read once at session finish.
_failing_test_files = set()


def pytest_configure(config):
    config._farever_scope = affected.plan_run(config)
    # Remember the narrowed plan so a later FULL run can attribute a red test
    # this run skipped (tests/affected.py, the mapping-gap ledger).
    affected.remember_plan(config._farever_scope, str(config.rootdir))
    _failing_test_files.clear()


def pytest_runtest_logreport(report):
    if report.failed:
        rel = affected.failing_test_rel(report.nodeid)
        if rel:
            _failing_test_files.add(rel)


def pytest_collectreport(report):
    """A collection error - a syntax error in a test module - counts too."""
    if report.failed:
        rel = affected.failing_test_rel(report.nodeid)
        if rel:
            _failing_test_files.add(rel)


def pytest_sessionfinish(session, exitstatus):
    """On a FULL run, note any red test file a narrowed run had skipped.

    Only a full run can see this: a narrowed run never executes the modules it
    dropped, so the gap stays invisible until something runs them and they
    fail. The record names the changed paths that should have selected them.
    """
    selection = getattr(session.config, "_farever_scope", None)
    full = selection is None or getattr(selection, "full", False)
    if not full or not _failing_test_files:
        return
    session.config._farever_gaps = affected.record_gaps(
        _failing_test_files, str(session.config.rootdir))


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    for line in affected.format_gaps(getattr(config, "_farever_gaps", [])):
        terminalreporter.write_line(line)


def pytest_report_header(config):
    selection = getattr(config, "_farever_scope", None)
    return affected.describe(selection) if selection is not None else None


def pytest_ignore_collect(collection_path, config):
    """Skip unselected test modules so they are never imported or run."""
    selection = getattr(config, "_farever_scope", None)
    if selection is None or selection.full:
        return None
    rel = affected.rel_test_path(collection_path)
    if rel is None:
        return None
    return rel not in selection.keep


@pytest.fixture(autouse=True)
def _flush_qt_deletions():
    """Deliver any queued widget deletions after every test.

    `widget.deleteLater()` only QUEUES a deletion, and on this PySide build
    `processEvents()` alone never delivers `QEvent.DeferredDelete` (see
    tests/qt_helpers.drain_deleted). Seven modules close their widgets with
    `deleteLater()` + `processEvents()` and never call the drain, so their
    trees were never freed: measured 2026-10-04, `tests/test_party_inspect.py`
    runs in 7.5 s alone but took 51 s inside the full suite, its single
    "does the page fit" test accounting for 48 s of that — every later build
    ran against tens of thousands of live widgets left by earlier modules.

    Flushing here is central (no test file has to opt in) and runs AFTER the
    test body, so no assertion observes it; it just makes the teardown those
    modules already wrote actually delete. A no-op when PySide is absent or no
    QApplication exists, which is how the non-Qt modules pay nothing.
    """
    yield
    try:
        from tests.qt_helpers import drain_deleted
    except Exception:
        return
    drain_deleted()


@pytest.fixture(scope="session", autouse=True)
def _no_working_set_trim():
    """Neutralize the app's Windows working-set trim inside the suite.

    A real ``ControlPanel`` scheduled two of them on construction: a 3 s
    ``QTimer.singleShot(_trim_working_set)`` and a 10 s repeating
    ``_mem_timer`` whose page sweeps also call it (``control_panel`` imported
    the name directly, so both spellings are patched). Tests build real panels
    and correctly never close them, so those timers keep firing for the rest of
    the session, and ``_trim_working_set`` starts with ``gc.collect()``.

    Measured 2026-10-05: a full run aborted at ~39% with a Windows
    heap-corruption abort (0xC0000374) whose faulthandler trace ended at that
    ``gc.collect()``, reached from pytest-qt's teardown ``processEvents()``.
    Ad-hoc scripts that built the same pages aborted with the same exit code.
    The trim is a memory-RESIDENCY optimization with no observable behaviour
    (nothing asserts it), so during tests it is a no-op; the app still trims.
    """
    try:
        from farever_companion.ui import control_panel as cp
        from farever_companion.ui import memory_reclaimer as mr
    except Exception:               # no PySide6: nothing schedules the trim
        yield
        return
    saved = mr._trim_working_set
    mr._trim_working_set = lambda: None
    cp._trim_working_set = lambda: None
    try:
        yield
    finally:
        mr._trim_working_set = saved
        cp._trim_working_set = saved


@pytest.fixture(scope="session", autouse=True)
def _isolated_moddata(tmp_path_factory):
    """Run the whole session against a THROWAWAY folder, never a live one.

    This used to pin the session to ``dist/dev-moddata``, which protects the
    frozen build's ``dist/moddata`` but NOT a source-tree user: ``run.py:66``
    pops ``FAREVER_DATA_FLAVOR``, so ``Run.bat`` writes to ``dist/dev-moddata``
    too. The suite and the live app therefore shared one folder, and any test
    that builds a bare ``Settings`` and saves writes over the user's real
    settings.json. It has not fired since (verified: five suites, md5 of the
    live file unchanged across each), but the hazard is one careless test away.

    So the target is now a per-session temp dir that no launcher can reach.

    The override is deliberately left in place for the LIFE OF THE PROCESS and
    never restored. Restoring it in a teardown ``finally`` looks tidy and is a
    trap: the session fixture finalizes BEFORE Qt teardown, so anything that
    saves settings afterwards — an OverlayManager or widget destructor closing
    its overlays, an atexit path — resolves ``config_dir()`` back to the live
    folder and writes a defaults settings.json over the user's.

    Measured 2026-09-19: every plain ``pytest tests/test_ui_utils.py`` run
    leaked ~10 ``defaults-instance ... open=[]`` audit lines into
    ``dist/moddata/settings_audit.log`` and wiped the user's overlay-restore
    list. The env var is process-scoped, so leaving it set costs nothing.
    """
    d = tmp_path_factory.mktemp("moddata") / "dev-moddata"
    d.mkdir(parents=True, exist_ok=True)
    os.environ["FAREVER_MODDATA_DIR"] = str(d)
    yield d
