"""Pre-build environment doctor: report every prerequisite at once, with fixes.

`build-mod.bat` preflights the same four things but aborts on the first miss,
so a half-configured machine needs one build run per problem to be diagnosed.
This runs them all and prints a single report:

    Python venv  - interpreter, version, and the packages the build imports
    Rust         - rustc / rustup, and whether a default toolchain is active
    MSVC         - the C++ toolchain `cl.exe`, found the same way the build does
    Windows SDK  - kernel32.lib, which the linker needs

Nothing here mutates the machine: every probe is read-only, and a check that
cannot run reports MISSING/WARN rather than guessing. The classification is
kept in pure helpers (candidates, summarize, render) so it is unit-testable
without spawning anything; `test_env_doctor.py` pins it.

Run one-shot with `doctor.bat` (which picks an interpreter even when `.venv`
is broken), or directly:

    .venv\\Scripts\\python.exe build_tools\\env_doctor.py

Exit code is 0 when nothing is MISSING (warnings are allowed) and 1 otherwise,
so a script can gate on it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"

# The distributions the build imports. Names are the PyPI distribution names
# `importlib.metadata` reports, paired with why the build needs each one.
VENV_REQUIREMENTS = (
    ("PySide6", "the Qt UI"),
    ("Pymem", "the fallback memory backend"),
    ("pillow", "icon handling"),
    ("maturin", "the farever_native build"),
    ("pyinstaller", "the frozen exe"),
)

# Minimum interpreter the codebase runs on (see CONTRIBUTING.md).
MIN_PYTHON = (3, 12)

RUSTUP_FIX = (
    "Install the Rust toolchain, then reopen the terminal so PATH picks up\n"
    "%USERPROFILE%\\.cargo\\bin:\n"
    "    winget install --id Rustlang.Rustup -e "
    "--accept-package-agreements --accept-source-agreements\n"
    "    or download from https://rustup.rs"
)
VENV_FIX = (
    "Create the venv and install the deps:\n"
    "    py -m venv .venv\n"
    "    .venv\\Scripts\\python.exe -m pip install -r requirements.txt"
)
MSVC_FIX = (
    "Install 'Build Tools for Visual Studio 2022' with the 'Desktop\n"
    "development with C++' workload (VC++ toolchain):\n"
    "    https://visualstudio.microsoft.com/visual-cpp-build-tools/"
)
SDK_FIX = (
    "The C++ tools are present but the Windows SDK is not. Any one of:\n"
    "    A (recommended): Visual Studio Installer > Modify > Build Tools >\n"
    "       Individual components > check 'Windows 11 SDK (10.0.22621)'\n"
    "    B: winget install --id Microsoft.WindowsSDK.10.0.22621 "
    "--accept-package-agreements --accept-source-agreements\n"
    "    C: https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/\n"
    "Then reopen the terminal so vcvars picks up the new SDK."
)

# Where the Windows 10/11 SDK lands; the versioned folder under Lib is globbed.
KITS_ROOT = Path(os.environ.get("ProgramFiles(x86)",
                                r"C:\Program Files (x86)")) / "Windows Kits"

# Asked of the *project* venv, so the doctor works even when run by a different
# interpreter. Names come in via argv; the report is one JSON object.
_VENV_PROBE = (
    "import importlib.metadata as m, json, sys\n"
    "def ver(n):\n"
    "    try:\n"
    "        return m.version(n)\n"
    "    except m.PackageNotFoundError:\n"
    "        return None\n"
    "print(json.dumps({n: ver(n) for n in sys.argv[1:]}))\n"
)

OK, WARN, MISSING = "OK", "WARN", "MISSING"


@dataclass
class Check:
    """One prerequisite's outcome and, when it is not OK, how to fix it."""

    name: str
    status: str
    detail: str
    fix: str = ""

    @property
    def ok(self) -> bool:
        """True unless the prerequisite is genuinely missing."""
        return self.status != MISSING


def _run(cmd, *, timeout: float = 120.0):
    """Run a probe, returning (returncode, combined output). 127 on OSError.

    A missing binary is the normal case here (that is what the doctor is for),
    so it becomes a probe result rather than an exception.
    """
    try:
        proc = subprocess.run(
            [str(part) for part in cmd],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _run_shell(command: str, *, timeout: float = 180.0):
    """Run one command *string* through the OS shell, returning (rc, output).

    The MSVC probe has to be a shell string: `cmd /c call "…vcvars64.bat" && …`
    passed through the argv list is mangled by CreateProcess (the quotes around
    the path survive literally), so the doctor would report a working toolchain
    as missing. A string lets cmd parse its own quotes.
    """
    try:
        proc = subprocess.run(command, shell=True, capture_output=True,
                              text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _has_default_toolchain(line: str) -> bool:
    """True for `rustup toolchain list` lines that rustup marks as default.

    rustup writes `(active, default)` for a toolchain that is both, and bare
    `(default)` otherwise - matching only the latter reported a perfectly good
    stable toolchain as having no default.
    """
    return "default" in line


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


def vcvars_candidates(env) -> list:
    """The two literal `vcvars64.bat` paths `build-mod.bat` tries first."""
    pf86 = env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    pf = env.get("ProgramFiles", r"C:\Program Files")
    tail = Path("Microsoft Visual Studio") / "2022" / "BuildTools" / "VC" \
        / "Auxiliary" / "Build" / "vcvars64.bat"
    return [Path(pf86) / tail, Path(pf) / tail]


def vswhere_path(env) -> Path:
    """Location of the Visual Studio installer's `vswhere.exe`."""
    pf86 = env.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return Path(pf86) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"


def sdk_kernel32_candidates(env, kits_root: Path = KITS_ROOT) -> list:
    """`kernel32.lib` locations: the active SDK first, then every installed one."""
    out = []
    sdk_dir, version = env.get("WindowsSdkDir"), env.get("WindowsSDKVersion")
    if sdk_dir and version:
        out.append(
            Path(sdk_dir) / "Lib" / version.strip("\\/") / "um" / "x64"
            / "kernel32.lib"
        )
    lib_root = kits_root / "10" / "Lib"
    if lib_root.is_dir():
        for folder in sorted(lib_root.glob("10.*")):
            out.append(folder / "um" / "x64" / "kernel32.lib")
    return out


def summarize(checks) -> int:
    """0 when nothing is MISSING, 1 otherwise. Warnings never fail the doctor."""
    return 1 if any(not check.ok for check in checks) else 0


def render(checks) -> str:
    """The human report: every item, and a fix under each one that needs it."""
    lines = ["", "FareverPal build environment", "=" * 30]
    for check in checks:
        lines.append(f"  [{check.status}] {check.name}: {check.detail}")
        if check.status != OK and check.fix:
            lines.extend(f"        {line}" for line in check.fix.splitlines())
    missing = [check.name for check in checks if not check.ok]
    lines.append("")
    if missing:
        lines.append(f"{len(missing)} prerequisite(s) MISSING: {', '.join(missing)}")
        lines.append("Fix the items above, then re-run doctor.bat.")
    else:
        lines.append("All prerequisites present - build-mod.bat should run.")
    lines.append("")
    return "\n".join(lines)


def check_python(python: Path = VENV_PYTHON, run=_run) -> Check:
    """The project venv: interpreter, version, and the build's imported deps."""
    name = "Python venv"
    if not Path(python).is_file():
        return Check(name, MISSING, f"{python} not found", VENV_FIX)
    code, out = run([python, "--version"])
    if code != 0:
        return Check(name, MISSING, f"{python}: {_first_line(out) or 'will not run'}",
                     VENV_FIX)
    version = _first_line(out)
    match = re.search(r"(\d+)\.(\d+)", version)
    if match and tuple(int(g) for g in match.groups()) < MIN_PYTHON:
        return Check(name, MISSING,
                     f"{version} (need {'.'.join(map(str, MIN_PYTHON))}+)", VENV_FIX)

    names = [dist for dist, _ in VENV_REQUIREMENTS]
    code, out = run([python, "-c", _VENV_PROBE, *names])
    try:
        found = json.loads(out) if code == 0 else {}
    except ValueError:
        found = {}
    absent = [dist for dist in names if not found.get(dist)]
    detail = version + ("; " + ", ".join(
        f"{dist} {found[dist]}" for dist in names if found.get(dist)
    ) if found else "")
    if absent:
        return Check(name, MISSING,
                     f"{version}; missing: {', '.join(absent)}", VENV_FIX)
    return Check(name, OK, detail)


def check_rust(which=shutil.which, run=_run) -> Check:
    """Rust is present AND has a default toolchain, or the build cannot start."""
    name = "Rust"
    code, out = run(["rustc", "--version"])
    if code == 0:
        line = _first_line(out)
        code, toolchains = run(["rustup", "toolchain", "list"])
        default = next(
            (ln.split()[0] for ln in toolchains.splitlines()
             if _has_default_toolchain(ln)),
            "",
        )
        if code == 0 and not default:
            return Check(name, WARN, f"{line}; no default toolchain active",
                         "Activate the stable toolchain:\n    rustup default stable")
        return Check(name, OK, line + (f"; default {default}" if default else ""))

    if which("rustup") is None:
        return Check(name, MISSING,
                     "neither 'rustc' nor 'rustup' found on PATH", RUSTUP_FIX)
    return Check(name, MISSING,
                 "'rustc' not on PATH but 'rustup' is - no default toolchain",
                 "rustup default stable")


def check_msvc(env=os.environ, run=_run_shell) -> Check:
    """`cl.exe` must resolve after vcvars64.bat, exactly as the build does."""
    name = "MSVC"
    tried = list(vcvars_candidates(env))
    vs = vswhere_path(env)
    if vs.is_file():
        code, out = run(f'"{vs}" -latest -products * -requires '
                        f'Microsoft.VisualStudio.Component.VC.Tools.x86.x64 '
                        f'-property installationPath')
        if code == 0:
            for line in out.splitlines():
                if line.strip():
                    tried.append(
                        Path(line.strip()) / "VC" / "Auxiliary" / "Build"
                        / "vcvars64.bat"
                    )

    for vcvars in tried:
        if not Path(vcvars).is_file():
            continue
        code, out = run(f'call "{vcvars}" >nul 2>&1 && where cl.exe')
        if code == 0 and "cl.exe" in out.lower():
            return Check(name, OK, f"cl.exe via {vcvars}")
    return Check(name, MISSING, "no vcvars64.bat / cl.exe found", MSVC_FIX)


def check_sdk(env=os.environ, kits_root: Path = KITS_ROOT) -> Check:
    """`kernel32.lib` must exist; the C++ tools alone do not provide it."""
    name = "Windows SDK"
    for candidate in sdk_kernel32_candidates(env, kits_root):
        if candidate.is_file():
            return Check(name, OK, str(candidate))
    return Check(name, MISSING, "kernel32.lib not found under Windows Kits",
                 SDK_FIX)


def gather(env=os.environ) -> list:
    """Run every prerequisite check (read-only) and return the outcomes."""
    return [
        check_python(),
        check_rust(),
        check_msvc(env),
        check_sdk(env),
    ]


def main() -> int:
    """Print the report and return the process exit code."""
    checks = gather()
    print(render(checks))
    return summarize(checks)


if __name__ == "__main__":
    raise SystemExit(main())
