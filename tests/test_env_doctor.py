"""The pre-build environment doctor reports the right status and the right fix.

Two failures this pins are worth naming, because both made a *working* machine
look broken and neither was visible from the report alone:

- `rustup toolchain list` marks a toolchain `(active, default)`, not `(default)`,
  so matching only the latter reported a healthy stable toolchain as having no
  default.
- The MSVC probe has to reach cmd as one shell string; passing
  `['cmd', '/c', 'call "…vcvars64.bat" && …']` lets CreateProcess keep the
  quotes literally, so cmd never ran the batch file and cl.exe "wasn't found".
"""
from __future__ import annotations

from pathlib import Path

from build_tools import env_doctor as ed


def test_only_a_missing_prerequisite_fails_the_doctor():
    """A warning must not fail the doctor; a missing prerequisite must."""
    checks = [
        ed.Check("Python venv", ed.OK, "ok"),
        ed.Check("Rust", ed.WARN, "no default toolchain", "rustup default stable"),
    ]
    assert ed.summarize(checks) == 0, "a warning must not fail the doctor"
    checks.append(ed.Check("MSVC", ed.MISSING, "cl.exe not found", "install"))
    assert ed.summarize(checks) == 1, "a missing prerequisite must fail the doctor"


def test_the_report_names_every_item_and_shows_its_fix():
    checks = [
        ed.Check("Rust", ed.MISSING, "neither rustc nor rustup on PATH",
                 ed.RUSTUP_FIX),
        ed.Check("Windows SDK", ed.OK, r"C:\kits\kernel32.lib"),
    ]
    report = ed.render(checks)
    assert "Rust" in report and "Windows SDK" in report
    assert "MISSING" in report and "OK" in report
    assert "rustup.rs" in report, "the fix must be shown under the failing item"


def test_rustup_active_and_default_counts_as_a_default_toolchain():
    """THE regression: rustup writes `(active, default)`, not `(default)`."""
    assert ed._has_default_toolchain("stable-x86_64-pc-windows-msvc (active, default)")
    assert ed._has_default_toolchain("stable-x86_64-pc-windows-msvc (default)")
    assert not ed._has_default_toolchain("nightly-x86_64-pc-windows-msvc")


def test_rust_present_but_with_no_default_is_only_a_warning():
    def run(cmd, **kw):
        if list(cmd)[0] == "rustc":
            return 0, "rustc 1.99.0 (b940084d7 2026-09-28)\n"
        return 0, "nightly-x86_64-pc-windows-msvc\n"

    check = ed.check_rust(which=lambda name: "rustup.exe", run=run)
    assert check.status == ed.WARN
    assert "rustup default stable" in check.fix


def test_no_rust_at_all_points_at_rustup():
    check = ed.check_rust(which=lambda name: None,
                          run=lambda cmd, **kw: (127, "not recognized"))
    assert check.status == ed.MISSING
    assert "rustup.rs" in check.fix and "Rustlang.Rustup" in check.fix


def test_a_missing_venv_names_the_create_command(tmp_path):
    check = ed.check_python(python=tmp_path / "nope" / "python.exe")
    assert check.status == ed.MISSING
    assert "py -m venv .venv" in check.fix


def test_the_msvc_probe_is_a_shell_string_so_cmd_parses_the_quotes(tmp_path):
    """THE regression: the argv-list form mangles the vcvars path's quotes."""
    env = {"ProgramFiles(x86)": str(tmp_path), "ProgramFiles": str(tmp_path)}
    vcvars = ed.vcvars_candidates(env)[0]      # where the doctor actually looks
    vcvars.parent.mkdir(parents=True, exist_ok=True)
    vcvars.write_text("@call vcvarsall.bat x64 %*\n", encoding="utf-8")
    seen: list = []

    def run(command, **kw):
        seen.append(command)
        return 0, r"C:\BuildTools\...\cl.exe"

    check = ed.check_msvc(env=env, run=run)
    assert check.status == ed.OK, check.detail
    assert all(isinstance(cmd, str) for cmd in seen), (
        "the MSVC probe must be one shell string, not an argv list"
    )
    assert f'call "{vcvars}"' in seen[-1]


def test_msvc_without_cl_reports_missing_and_the_install_fix(tmp_path):
    env = {"ProgramFiles(x86)": str(tmp_path), "ProgramFiles": str(tmp_path)}
    vcvars = ed.vcvars_candidates(env)[0]
    vcvars.parent.mkdir(parents=True, exist_ok=True)
    vcvars.write_text("@call vcvarsall.bat x64 %*\n", encoding="utf-8")
    check = ed.check_msvc(env=env, run=lambda command, **kw: (1, ""))
    assert check.status == ed.MISSING
    assert "visual-cpp-build-tools" in check.fix


def test_the_sdk_search_covers_the_active_sdk_and_every_installed_one(tmp_path):
    lib = tmp_path / "10" / "Lib" / "10.0.22621.0" / "um" / "x64"
    lib.mkdir(parents=True)
    (lib / "kernel32.lib").write_bytes(b"")
    env = {"WindowsSdkDir": r"C:\SDKs", "WindowsSDKVersion": r"10.0.19041.0\\"}
    found = [str(p) for p in ed.sdk_kernel32_candidates(env, tmp_path)]
    assert r"C:\SDKs\Lib\10.0.19041.0\um\x64\kernel32.lib" in found, (
        "the active WindowsSdkDir must be checked first"
    )
    assert any("10.0.22621.0" in p and p.endswith("kernel32.lib") for p in found), (
        "every installed SDK version must be searched"
    )


def test_a_missing_sdk_reports_missing_with_options():
    check = ed.check_sdk(env={}, kits_root=Path("Z:/does/not/exist"))
    assert check.status == ed.MISSING
    assert "WindowsSDK" in check.fix
