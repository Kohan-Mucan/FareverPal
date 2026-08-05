@echo off
setlocal
cd /d %~dp0

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
:: Check the same way the spec does, and abort instead of building that exe.
set "RAW_DATA_SIZE=0"
if exist "farever_companion\data\raw_data.py" for %%A in ("farever_companion\data\raw_data.py") do set "RAW_DATA_SIZE=%%~zA"
if %RAW_DATA_SIZE% GTR 1000 (
    echo JSON packing: OFF - raw_data.py active ^(%RAW_DATA_SIZE% bytes^), no loose .json files will be bundled.
) else (
    echo.
    powershell -Command "Write-Host '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!' -ForegroundColor Red"
    powershell -Command "Write-Host '   JSON PACKING DETECTED - ABORTING BUILD        ' -ForegroundColor White -BackgroundColor Red"
    powershell -Command "Write-Host '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!' -ForegroundColor Red"
    echo.
    echo raw_data.py is missing or too small ^(%RAW_DATA_SIZE% bytes^), so FareverPal.spec
    echo would fall back to bundling assets/data/*.json into the exe. Loose JSONs
    echo should never ship. Fix the data first, then re-run:
    echo   1. Extract the game sheets into assets/data
    echo   2. Run Update_Raw_Data.bat to regenerate the raw_*.py shims
    goto err
)

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
set "VERIFY_SCRIPT=packaging\verify_assets.py"

.venv\Scripts\python.exe "%VERIFY_SCRIPT%"
if errorlevel 1 (
    echo.
    powershell -Command "Write-Host '--------------------------------------------------' -ForegroundColor Red"
    powershell -Command "Write-Host '   BUILD INCOMPLETE - MISSING CRITICAL DATA      ' -ForegroundColor Red -BackgroundColor Black"
    powershell -Command "Write-Host '--------------------------------------------------' -ForegroundColor Red"
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
