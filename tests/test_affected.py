"""The affected-test selector: mapping, fail-open, and the scope switch.

`tests/affected.py` decides which test modules a dirty working tree keeps, so
these pin the three ways it finds one (import closure, a textual mention, an area
guard) and the four ways it refuses to narrow (CI, an explicit selection, nothing
changed, an unmapped Python source). They also pin the mapping-gap ledger: the
plan a narrowed run remembers, and the gap a later full run records when it goes
red in a test file that plan skipped. Everything below the config-level tests
runs against a synthetic tree, so it asserts the algorithm rather than this
repo's current shape.
"""
from __future__ import annotations

import json

from tests import affected


def _texts():
    return {
        "app/__init__.py": "",
        "app/a.py": "from app import b\n",
        "app/b.py": "import os\n",
        "app/c.py": "def f():\n    from app.d import thing\n",
        "app/d.py": "",
        "tests/test_a.py": "from app import a\n",
        "tests/test_b.py": "from app.a import thing\n",
        "tests/test_c.py": "from app import c\n",
        "tests/test_lone.py": "import unrelated\n",
        "tests/test_data.py": 'SHEET = "assets/data/thing.json"\n',
        "tests/test_repo_hygiene.py": "",
        "tests/test_ui_utils.py": "",
    }


def test_a_changed_module_keeps_the_tests_that_import_it():
    selection = affected.select_tests(["app/a.py"], _texts())
    assert {"tests/test_a.py", "tests/test_b.py"} <= selection.keep


def test_a_dependency_chain_is_followed_transitively():
    # test_a imports app -> which imports app.b; a change to app.b must keep it.
    selection = affected.select_tests(["app/b.py"], _texts())
    assert "tests/test_a.py" in selection.keep


def test_a_deferred_import_inside_a_function_still_counts():
    selection = affected.select_tests(["app/d.py"], _texts())
    assert "tests/test_c.py" in selection.keep


def test_a_test_that_names_the_changed_file_is_kept():
    selection = affected.select_tests(["assets/data/thing.json"], _texts())
    assert "tests/test_data.py" in selection.keep


def test_a_test_that_neither_imports_nor_names_the_change_is_left_out():
    selection = affected.select_tests(["app/a.py"], _texts())
    assert "tests/test_lone.py" not in selection.keep


def test_the_tree_wide_contract_test_is_always_kept():
    selection = affected.select_tests(["app/lonely.py"], _texts())
    assert "tests/test_repo_hygiene.py" in selection.keep


def test_a_ui_change_also_keeps_the_whole_ui_scan():
    selection = affected.select_tests(["farever_companion/ui/widgets.py"], _texts())
    assert "tests/test_ui_utils.py" in selection.keep


def test_a_source_no_test_reaches_is_reported_as_unmapped():
    selection = affected.select_tests(["app/lonely.py"], _texts())
    assert selection.unmapped == ["app/lonely.py"]


def test_an_edited_test_module_keeps_itself():
    selection = affected.select_tests(["tests/test_lone.py"], _texts())
    assert "tests/test_lone.py" in selection.keep


def test_status_parsing_keeps_changes_and_reads_a_rename_as_its_new_path():
    # `XY <path>` records, NUL-separated; `R  new\0old` is the -z rename shape,
    # so the source field must be skipped rather than parsed as its own record.
    payload = "\0".join([
        " M farever_companion/paths.py",
        "?? tests/test_new.py",
        "R  tests/test_new_name.py",
        "tests/test_old_name.py",
        "",
    ])
    assert affected._parse_status(payload) == [
        "farever_companion/paths.py",
        "tests/test_new.py",
        "tests/test_new_name.py",
    ]


class _Option:
    keyword = None
    markexpr = None
    deselect = None
    last_failed = False
    stepwise = False


class _Config:
    def __init__(self, scope=None, args=(), rootdir="ROOTDIR"):
        self._scope = scope
        self.option = _Option()
        self.args = list(args)
        self.rootdir = rootdir

    def getoption(self, name):
        return self._scope


def _plan(monkeypatch, changed, config=None):
    monkeypatch.setattr(affected, "changed_paths", lambda root=affected.ROOT: changed)
    return affected.plan_run(config or _Config())


def test_plan_is_none_when_nothing_changed(monkeypatch):
    assert _plan(monkeypatch, []) is None


def test_plan_is_none_when_git_cannot_be_read(monkeypatch):
    assert _plan(monkeypatch, None) is None


def test_plan_is_none_under_ci(monkeypatch):
    monkeypatch.setenv("CI", "true")
    assert _plan(monkeypatch, ["tests/test_a.py"]) is None


def test_plan_is_none_when_scope_is_all(monkeypatch):
    assert _plan(monkeypatch, ["tests/test_a.py"], _Config(scope="all")) is None


def test_plan_is_none_when_a_path_was_named(monkeypatch):
    config = _Config(args=["tests/test_a.py"])
    assert _plan(monkeypatch, ["tests/test_a.py"], config) is None


def test_plan_is_none_when_a_keyword_filter_was_given(monkeypatch):
    config = _Config()
    config.option.keyword = "something"
    assert _plan(monkeypatch, ["tests/test_a.py"], config) is None


def test_a_conftest_change_falls_back_to_the_full_suite(monkeypatch):
    selection = _plan(monkeypatch, ["tests/conftest.py"])
    assert selection is not None and selection.full


def _stub_selection(monkeypatch, unmapped):
    monkeypatch.setattr(affected, "changed_paths",
                        lambda root=affected.ROOT: ["app/x.py"])
    monkeypatch.setattr(
        affected, "select_tests",
        lambda changed, texts=None: affected.Selection(
            ("tests/test_a.py",), changed, unmapped))
    return affected.plan_run(_Config())


def test_an_unmapped_python_source_falls_back_to_the_full_suite(monkeypatch):
    selection = _stub_selection(monkeypatch, ["app/x.py"])
    assert selection is not None and selection.full


def test_an_unmapped_data_file_does_not_force_the_full_suite(monkeypatch):
    selection = _stub_selection(monkeypatch, ["assets/data/thing.json"])
    assert selection is not None and not selection.full


def test_plan_keeps_an_ordinary_python_change_narrow(monkeypatch):
    selection = _plan(monkeypatch, ["tests/test_a.py"])
    assert selection is not None and not selection.full
    assert "tests/test_repo_hygiene.py" in selection.keep


def test_rel_test_path_selects_only_test_modules_under_tests():
    root = affected.ROOT
    assert affected.rel_test_path(root / "tests" / "test_ui_utils.py") == \
        "tests/test_ui_utils.py"
    assert affected.rel_test_path(root / "tests" / "qt_helpers.py") is None
    assert affected.rel_test_path(root / "tests") is None
    assert affected.rel_test_path(root / "farever_companion" / "paths.py") is None


class _ScopeConfig:
    """Just enough config for the conftest hooks."""

    def __init__(self, selection):
        self._farever_scope = selection


def test_the_ignore_hook_keeps_only_the_selected_test_modules():
    from tests import conftest

    config = _ScopeConfig(affected.Selection(("tests/test_affected.py",),
                                             ["app/a.py"]))
    root = affected.ROOT / "tests"
    assert conftest.pytest_ignore_collect(root / "test_ui_utils.py", config)
    assert conftest.pytest_ignore_collect(root / "test_affected.py", config) is False
    assert conftest.pytest_ignore_collect(root / "qt_helpers.py", config) is None
    assert conftest.pytest_ignore_collect(root, config) is None


def test_the_ignore_hook_stands_down_for_a_full_selection():
    from tests import conftest

    config = _ScopeConfig(affected.Selection((), [], full=True))
    assert conftest.pytest_ignore_collect(
        affected.ROOT / "tests" / "test_ui_utils.py", config) is None


def test_the_ignore_hook_stands_down_when_no_plan_was_made():
    from tests import conftest

    assert conftest.pytest_ignore_collect(
        affected.ROOT / "tests" / "test_ui_utils.py", _ScopeConfig(None)) is None


def test_describe_names_the_scope_and_how_to_widen_it():
    narrowed = affected.describe(
        affected.Selection(("tests/test_a.py",), ["app/a.py"]))
    assert any("affected test file" in line for line in narrowed)
    assert any("--scope=all" in line for line in narrowed)
    full = affected.describe(affected.Selection((), ["app/a.py"], full=True))
    assert len(full) == 1 and "full suite" in full[0]


def test_the_real_repo_maps_a_ui_change_to_its_owning_tests():
    selection = affected.select_tests(
        ["farever_companion/ui/pages/combat_roster.py"])
    assert "tests/test_repo_hygiene.py" in selection.keep
    assert "tests/test_ui_utils.py" in selection.keep
    assert not selection.full


# --- mapping-gap ledger -----------------------------------------------------


def test_failing_test_rel_reads_a_module_out_of_a_nodeid():
    assert affected.failing_test_rel("tests/test_x.py::test_y[1]") == "tests/test_x.py"
    assert affected.failing_test_rel("tests/test_x.py") == "tests/test_x.py"
    assert affected.failing_test_rel("tests/qt_helpers.py") is None
    assert affected.failing_test_rel("farever_companion/paths.py::test") is None


def test_mapping_gaps_names_only_the_files_the_plan_skipped():
    plan = {"changed": ["app/a.py"], "keep": ["tests/test_a.py"]}
    gaps = affected.mapping_gaps(
        {"tests/test_a.py", "tests/test_skipped.py"}, plan)
    assert gaps == [("tests/test_skipped.py", ["app/a.py"])]


def test_mapping_gaps_is_empty_when_every_red_file_was_kept():
    plan = {"changed": ["app/a.py"], "keep": ["tests/test_a.py"]}
    assert affected.mapping_gaps({"tests/test_a.py"}, plan) == []


def test_remember_plan_ignores_a_full_or_absent_selection(tmp_path):
    assert affected.remember_plan(None, tmp_path) is None
    # A full selection can still carry changed paths (a conftest change is the
    # worked example), so `full` must be rejected on its own - the empty-changed
    # case alone would pass even if the `full` test were dropped.
    full = affected.Selection((), ["tests/conftest.py"], full=True)
    assert affected.remember_plan(full, tmp_path) is None, (
        "a full run has no plan to remember, and writing one would erase the "
        "narrowed plan the next full run reads")
    assert not affected.ledger_path(tmp_path).exists()


def test_a_full_selection_does_not_overwrite_a_remembered_plan(tmp_path):
    affected.remember_plan(
        affected.Selection(("tests/test_a.py",), ["app/a.py"]), tmp_path)
    before = affected.ledger_path(tmp_path).read_text(encoding="utf-8")
    affected.remember_plan(
        affected.Selection((), ["tests/conftest.py"], full=True), tmp_path)
    assert affected.ledger_path(tmp_path).read_text(encoding="utf-8") == before


def test_record_gaps_writes_the_red_file_and_its_changed_paths(tmp_path):
    affected.remember_plan(
        affected.Selection(("tests/test_a.py",), ["app/a.py"]), tmp_path)
    gaps = affected.record_gaps({"tests/test_skipped.py"}, tmp_path)
    assert gaps == [("tests/test_skipped.py", ["app/a.py"])]
    stored = json.loads(affected.ledger_path(tmp_path).read_text(encoding="utf-8"))
    entry = stored["gaps"]["tests/test_skipped.py"]
    assert entry["changed"] == ["app/a.py"]
    assert entry["seen"] == 1


def test_record_gaps_counts_a_recurring_gap(tmp_path):
    affected.remember_plan(
        affected.Selection(("tests/test_a.py",), ["app/a.py"]), tmp_path)
    affected.record_gaps({"tests/test_skipped.py"}, tmp_path)
    affected.record_gaps({"tests/test_skipped.py"}, tmp_path)
    stored = json.loads(affected.ledger_path(tmp_path).read_text(encoding="utf-8"))
    assert stored["gaps"]["tests/test_skipped.py"]["seen"] == 2


def test_record_gaps_needs_a_remembered_plan(tmp_path):
    assert affected.record_gaps({"tests/test_skipped.py"}, tmp_path) == []


def test_format_gaps_names_the_file_the_paths_and_the_fix():
    text = "\n".join(affected.format_gaps(
        [("tests/test_skipped.py", ["app/a.py"])]))
    assert "tests/test_skipped.py" in text
    assert "app/a.py" in text
    assert "_ALWAYS_TESTS" in text
    assert affected.format_gaps([]) == []


class _Session:
    def __init__(self, config):
        self.config = config


def _full_config(rootdir):
    config = _Config()
    config.rootdir = str(rootdir)
    config._farever_scope = None            # a full run: nothing was narrowed
    return config


def test_a_full_run_records_a_gap_a_narrowed_run_left(tmp_path, monkeypatch):
    from tests import conftest

    affected.remember_plan(
        affected.Selection(("tests/test_a.py",), ["app/a.py"]), tmp_path)
    config = _full_config(tmp_path)
    monkeypatch.setattr(conftest, "_failing_test_files",
                        {"tests/test_skipped.py"})
    conftest.pytest_sessionfinish(_Session(config), 1)
    assert config._farever_gaps == [("tests/test_skipped.py", ["app/a.py"])]


def test_configure_remembers_a_narrowed_plan(tmp_path, monkeypatch):
    from tests import conftest

    monkeypatch.setattr(affected, "plan_run", lambda config: affected.Selection(
        ("tests/test_a.py",), ["app/a.py"]))
    config = _Config()
    config.rootdir = str(tmp_path)
    conftest.pytest_configure(config)
    stored = json.loads(affected.ledger_path(tmp_path).read_text(encoding="utf-8"))
    assert stored["plan"] == {"changed": ["app/a.py"],
                              "keep": ["tests/test_a.py"]}


def test_configure_remembers_nothing_on_a_full_run(tmp_path, monkeypatch):
    from tests import conftest

    monkeypatch.setattr(affected, "plan_run", lambda config: None)
    config = _Config()
    config.rootdir = str(tmp_path)
    conftest.pytest_configure(config)
    assert not affected.ledger_path(tmp_path).exists()


def test_a_narrowed_run_records_nothing(tmp_path, monkeypatch):
    from tests import conftest

    affected.remember_plan(
        affected.Selection(("tests/test_a.py",), ["app/a.py"]), tmp_path)
    config = _Config()
    config.rootdir = str(tmp_path)
    config._farever_scope = affected.Selection(
        ("tests/test_a.py",), ["app/a.py"])
    monkeypatch.setattr(conftest, "_failing_test_files",
                        {"tests/test_skipped.py"})
    conftest.pytest_sessionfinish(_Session(config), 1)
    assert not hasattr(config, "_farever_gaps")
