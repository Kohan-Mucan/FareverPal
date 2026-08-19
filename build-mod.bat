@echo off
setlocal
cd /d %~dp0

REM ------------------------------------------------------------------
REM Packaging notes
REM ------------------------------------------------------------------
REM 1. Console-script shims: every build step invokes tools via
REM    .venv\Scripts\python.exe -m ... on purpose, so a broken launcher
REM    shim (e.g. an old distlib shim that exits 1 with no output) can
REM    silently skip the venv tooling without failing the build. The
REM    [5/5] verify step probes the console launchers to catch exactly
REM    that and now hard-fails the build. If the plain pyinstaller.exe
REM    ever exits silently again, regenerate its launcher with:
REM      .venv\Scripts\python.exe -m pip install --force-reinstall --no-deps "pyinstaller==6.20.0"
REM    (Same one-liner applies to pip / pytest / maturin / Pygments /
REM    PySide6_Essentials if their launchers break. Note the pyside6-*
REM    shims belong to PySide6_Essentials, not the PySide6 wheel.)
REM 2. qoffscreen plugin: FareverPal.spec's QT_KEEP whitelist must keep
REM    qoffscreen.dll so the frozen exe can initialize Qt headlessly.
REM    The FAREVER_OPEN_PAGE / FAREVER_SCREENSHOT frozen smoke tests run
REM    with QT_QPA_PLATFORM=offscreen and rely on it; without the plugin
REM    the exe pops a "no Qt platform plugin" dialog instead of rendering.
REM ------------------------------------------------------------------

echo [1/5] Building Rust memory reader (farever_native)...
set PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1
REM Strip the leaked cargo/user path from panic strings in the shipped binary
REM (otherwise the absolute %USERPROFILE% build path leaks into release panics).
set RUSTFLAGS=--remap-path-prefix=%USERPROFILE%=.
.venv\Scripts\python.exe -m maturin develop --release -m native\Cargo.toml
if errorlevel 1 goto err
set "RUSTFLAGS="

echo [2/5] Import sanity check...
.venv\Scripts\python.exe -c "import farever_native; print('farever_native', farever_native.__version__)"
if errorlevel 1 goto err

echo.
echo [3/5] Compiling game database (embedded raw_*.py shims)...
.venv\Scripts\python.exe compiler.py
if errorlevel 1 goto err

echo.
echo [4/5] Packaging executable...

:: Never ship loose .json files. FareverPal.spec bundles assets/data/*.json
:: into the exe only when raw_data.py is missing or too small (fallback mode).
:: The Item / Craft pages read their sheets from dedicated shims too
:: (raw_item_drops.py / raw_craft.py), so every shim must be present before
:: we package — same shims the spec's hiddenimports rely on.
set "RAW_DATA_SIZE=0"
if exist "farever_companion\data\raw_data.py" for %%A in ("farever_companion\data\raw_data.py") do set "RAW_DATA_SIZE=%%~zA"
set "RAW_CRAFT_SIZE=0"
if exist "farever_companion\data\raw_craft.py" for %%A in ("farever_companion\data\raw_craft.py") do set "RAW_CRAFT_SIZE=%%~zA"
set "RAW_DROPS_SIZE=0"
if exist "farever_companion\data\raw_item_drops.py" for %%A in ("farever_companion\data\raw_item_drops.py") do set "RAW_DROPS_SIZE=%%~zA"
if %RAW_DATA_SIZE% LEQ 1000 goto shim_err
if %RAW_CRAFT_SIZE% LEQ 1000 goto shim_err
if %RAW_DROPS_SIZE% LEQ 1000 goto shim_err
echo JSON packing: OFF - shims active ^(raw_data=%RAW_DATA_SIZE% B, raw_craft=%RAW_CRAFT_SIZE% B, raw_item_drops=%RAW_DROPS_SIZE% B^), no loose .json files will be bundled.
goto shims_ok

:shim_err
echo.
powershell -Command "Write-Host '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!' -ForegroundColor Red"
powershell -Command "Write-Host '   EMBEDDED SHIM MISSING - ABORTING BUILD       ' -ForegroundColor White -BackgroundColor Red"
powershell -Command "Write-Host '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!' -ForegroundColor Red"
echo.
echo One of the embedded raw_*.py shims is missing or too small:
echo   raw_data.py       ^(%RAW_DATA_SIZE% bytes^)
echo   raw_craft.py      ^(%RAW_CRAFT_SIZE% bytes^)
echo   raw_item_drops.py ^(%RAW_DROPS_SIZE% bytes^)
echo The exe must carry these shims ^(FareverPal.spec hiddenimports^); loose
echo JSONs should never ship. Fix the data first, then re-run:
echo   1. Extract the game sheets into assets/data
echo   2. Run Update_Raw_Data.bat to regenerate the raw_*.py shims
goto err

:shims_ok

:: Extract Version-like info from the latest Git commit message
:: (e.g., if message is "v 0.3.0 Compass Fix", it extracts "v_0_3_0")
for /f "tokens=1,2" %%a in ('git log -1 --format^="%%s"') do (
    set "PART1=%%a"
    set "PART2=%%b"
)
if /i "%PART1%"=="v" (
    set "GIT_VER=V_%PART2:.=_%"
) else (
    set "GIT_VER=%PART1:.=_%"
)

:: Get short Git commit hash to uniquely identify the build
for /f "tokens=*" %%i in ('git rev-parse --short HEAD') do set GIT_REV=%%i

:: Define name (e.g., FareverPal-V_0_3_0-a1b2c3d)
set "EXE_NAME=FareverPal-%GIT_VER%-%GIT_REV%"

.venv\Scripts\python.exe -m PyInstaller --noconfirm FareverPal.spec
if errorlevel 1 goto err

:: Rename output to versioned name
if exist "dist\FareverPal.exe" (
    echo.
    echo Renaming dist\FareverPal.exe to dist\%EXE_NAME%.exe
    move /y "dist\FareverPal.exe" "dist\%EXE_NAME%.exe" >nul
    if errorlevel 1 (
        echo.
        echo ERROR: could not rename dist\FareverPal.exe to dist\%EXE_NAME%.exe.
        echo Close any running FareverPal instance and re-run the build.
        goto err
    )
)

echo.
echo [5/5] Validating Build Integrity...
set "VERIFY_SCRIPT=build_tools\verify_assets.py"

.venv\Scripts\python.exe "%VERIFY_SCRIPT%"
if errorlevel 1 (
    echo.
    powershell -Command "Write-Host '--------------------------------------------------' -ForegroundColor Red"
    powershell -Command "Write-Host '   BUILD INCOMPLETE - MISSING CRITICAL DATA      ' -ForegroundColor Red -BackgroundColor Black"
    powershell -Command "Write-Host '--------------------------------------------------' -ForegroundColor Red"
    :: A failed verification (missing data OR a broken console-script shim)
    :: must fail the build, not just print a banner and exit 0.
    goto err
) else (
    echo.
    powershell -Command "Write-Host '--------------------------------------------------' -ForegroundColor Green"
    powershell -Command "Write-Host '    100%% DONE - ALL ASSETS VERIFIED \o/           ' -ForegroundColor Green -BackgroundColor Black"
    powershell -Command "Write-Host '--------------------------------------------------' -ForegroundColor Green"
    echo If it built, the exe is in dist\%EXE_NAME%.exe
)

echo.
pause
goto :eof

:err
echo.
powershell -Command "Write-Host '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!' -ForegroundColor Red"
powershell -Command "Write-Host '             CRITICAL BUILD FAILURE               ' -ForegroundColor White -BackgroundColor Red"
powershell -Command "Write-Host '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!' -ForegroundColor Red"
pause
exit /b 1
