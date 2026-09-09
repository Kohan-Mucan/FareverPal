@echo off
setlocal
cd /d %~dp0
echo ============================================================
echo  FareverPal - Startup Import Check
echo ============================================================
echo.
echo  Imports the app entry chain (run.py / app / control_panel /
echo  combat_page / data.items) and checks every items-package
echo  export resolves. Catches the "Startup Error" ImportError
echo  before you launch the app.
echo.

:: Prefer the project venv (PySide6 lives there); fall back to python on PATH.
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -m pytest --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: pytest not found for "%PY%"
    echo Install it with:  "%PY%" -m pip install pytest
    echo.
    pause
    exit /b 1
)

:: Headless Qt: the chain imports PySide6 but never opens a window.
set "QT_QPA_PLATFORM=offscreen"

"%PY%" -m pytest tests\test_startup_smoke.py -v
if errorlevel 1 (
    echo.
    echo ============================================================
    echo  FAILURE: the app entry chain does not import cleanly.
    echo  Fix the ImportError above before launching FareverPal.
    echo ============================================================
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  SUCCESS: startup import chain is clean.
echo ============================================================
echo.
pause
endlocal
