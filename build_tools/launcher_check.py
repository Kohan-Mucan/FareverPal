"""Pure helpers for the venv console-script launcher check.

Stale venv shims (distlib launchers written by an old pip, whose companion
``-script.py`` files were never installed) fail with a very specific
signature: they exit almost immediately with code 1 and print *nothing at
all*. A working launcher either exits cleanly, prints something (help /
version / usage / a traceback from the real tool), or stays alive (GUI
tools). These classifiers keep that logic unit-testable without spawning
processes; the process-driving loop lives in verify_assets.py.

Probing notes:
- Only pure *console* tools are executed by the check (pip, pytest,
  maturin, pyinstaller, pyi-*, crashlink): they print to stdout and exit
  immediately. Everything else is verified statically, never run.
- ``pyside6-*`` launchers are GUI-subsystem or Qt-native binaries that open
  a window — or a modal help dialog — when launched with --help, even with
  ``QT_QPA_PLATFORM=offscreen`` set (the wrapper chain swallows it).
  Launching them during a build would pop windows on the user's desktop,
  so ``is_static_only()`` keeps them out of the run-probe loop entirely;
  they are verified by checking the launcher and its wrapped binary exist.
"""

# Tools that print their version when given a version flag (faster and more
# deterministic than --help). Everything else is probed with --help: argparse
# tools print help and exit 0, while native tools that reject --help still
# print an error and exit nonzero — classify() treats that as "tool ran".
VERSION_PROBES = {
    "pip": "--version",
    "pip3": "--version",
    "pip3.14": "--version",
    "pytest": "--version",
    "py.test": "--version",
    "maturin": "--version",
    "pyinstaller": "--version",
    "pygmentize": "-V",
}

TIMEOUT_SECONDS = 5.0


def is_static_only(name: str) -> bool:
    """True for launchers verified without execution.

    ``pyside6-*`` tools open windows / modal help dialogs when launched, so
    the check never executes them (see the module docstring)."""
    return name.startswith("pyside6-")


def wrapped_target(name: str, scripts_dir):
    """The real file a ``pyside6-*`` launcher front-ends.

    Every pyside6 entry point runs ``pyside_tool.<tool>``, which either
    wraps a native binary (``PySide6/<tool>.exe``), a python script
    (``PySide6/scripts/<tool>.py``), or genpyi's ``support/generate_pyi.py``.
    Returns None for non-PySide6 launchers.
    """
    if not name.startswith("pyside6-"):
        return None
    pyside_dir = (scripts_dir.parent / "Lib" / "site-packages" / "PySide6")
    tool = name[len("pyside6-"):]
    if tool == "genpyi":
        return pyside_dir / "support" / "generate_pyi.py"
    if tool in {"deploy", "metaobjectdump", "project", "qml", "qtpy2cpp"}:
        return pyside_dir / "scripts" / f"{tool}.py"
    return pyside_dir / f"{tool}.exe"


def probe_args(name: str) -> list:
    """Command-line args used to smoke-test the launcher for ``name``."""
    return [VERSION_PROBES.get(name, "--help")]


def classify(name: str, returncode, output: str, still_running: bool):
    """Classify one launcher probe result into (status, detail).

    status is one of ``"ok"``, ``"warn"`` or ``"broken"``:

    - ``still_running`` — the process launched the real tool and kept
      running (GUI app in its event loop): ok.
    - exit 0 — clean run: ok.
    - nonzero with output — the tool ran; the probe flag was just
      unsupported by it (e.g. ``lrelease`` wants ``-help``): warn, not a
      launcher failure.
    - nonzero with no output — the stale-shim signature: broken.
    """
    if still_running:
        return "ok", "launched (GUI), still running"
    output = (output or "").strip()
    if returncode == 0:
        return "ok", _first_line(output) or "clean exit"
    if output:
        return "warn", (
            f"exit {returncode} (tool ran, probe flag unsupported): "
            f"{_first_line(output)}"
        )
    return "broken", "silent exit — stale console-script shim"


def _first_line(text: str, limit: int = 70) -> str:
    line = text.splitlines()[0].strip() if text else ""
    # Tool output may contain bytes that decode to characters a cp1252
    # console cannot print (e.g. "\ufffd") — escape them so the report line
    # always renders.
    line = line.encode("ascii", "backslashreplace").decode("ascii")
    if len(line) > limit:
        line = line[: limit - 3] + "..."
    return line
