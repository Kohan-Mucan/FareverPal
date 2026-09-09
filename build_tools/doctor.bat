@echo off
REM ------------------------------------------------------------------
REM One-shot build-environment doctor (see build_tools/env_doctor.py).
REM Reports every prerequisite the build needs - Python venv, Rust,
REM MSVC C++ tools, Windows SDK - each with the exact fix. Read-only;
REM it installs and builds nothing.
REM
REM   build_tools\doctor.bat          <- report, pauses (double-click)
REM   build_tools\doctor.bat fast     <- any argument skips the pause
REM
REM It picks an interpreter that exists even when .venv is broken, so
REM it can tell you the venv itself is the problem. build-mod.bat runs
REM the same preflights but stops at the first miss.
REM ------------------------------------------------------------------
setlocal
pushd "%~dp0.."
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY (
    where py >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
    echo ERROR: no Python found - neither .venv\Scripts\python.exe nor
    echo 'py'/'python' is on PATH. Install Python 3.12+ from
    echo https://www.python.org/downloads/ , then re-run this doctor.
    popd
    if "%~1"=="" pause
    exit /b 1
)

%PY% "build_tools\env_doctor.py" %*
set "RC=%errorlevel%"
popd

if "%~1"=="" pause
exit /b %RC%
