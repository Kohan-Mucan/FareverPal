@echo off

REM ------------------------------------------------------------------
REM Build transcript. This script re-invokes ITSELF exactly once through
REM a PowerShell tee, so the whole run lands in build-mod.log while the
REM output still streams to the console as it is produced. A packaging
REM failure used to leave nothing behind but a console window the user
REM then closed, taking the PyInstaller / shim-contract output with it.
REM
REM FAREVER_BUILD_LOG_TEE is the guard: the outer pass sets it and the
REM inner pass (all the real work lives under :run_build) jumps straight
REM past the tee. Same tee form as dps_bridge\build_all.bat, for the same
REM two reasons: Tee-Object writes UTF-16 in Windows PowerShell, and
REM '' + $_ keeps a tool's stderr out of PowerShell's NativeCommandError
REM framing. The Write-Host banners further down still reach the log.
REM
REM This is deliberately goto-based and NOT one parenthesized block: cmd
REM parses a whole block before running any of it, so every %VAR% set
REM inside one still expands empty. That bug shipped a transcript written
REM to "" and lost the run's output entirely.
REM ------------------------------------------------------------------
if defined FAREVER_BUILD_LOG_TEE goto :run_build

setlocal
set "FAREVER_BUILD_LOG_TEE=1"
set "FAREVER_BUILD_LOG=%~dp0build-mod.log"
> "%FAREVER_BUILD_LOG%" echo === FareverPal mod build - %DATE% %TIME% ===
>>"%FAREVER_BUILD_LOG%" echo === script: %~nx0   launched from %CD%
>>"%FAREVER_BUILD_LOG%" echo.

REM Never advertise a transcript that does not exist. A path to a file that
REM was never written is worse than no logging at all, because it reads as
REM "logging is working" -- which is exactly how the first version of this
REM block failed: %FAREVER_BUILD_LOG% expanded empty inside a block, so the
REM line below printed a bare path and Out-File wrote nothing.
if exist "%FAREVER_BUILD_LOG%" (
    for %%A in ("%FAREVER_BUILD_LOG%") do set "_HDRSIZE=%%~zA"
    echo Build transcript: %FAREVER_BUILD_LOG%
) else (
    echo [WARN] Could not create "%FAREVER_BUILD_LOG%" - no transcript will be kept.
    set "FAREVER_BUILD_LOG="
)
echo.

REM A missing log must never be what breaks the build.
if not defined FAREVER_BUILD_LOG goto :tee_plain
where powershell.exe >nul 2>&1
if not errorlevel 1 goto :tee_run
echo [WARN] powershell.exe not found - no build transcript will be written.
:tee_plain
call "%~f0"
goto :tee_done

:tee_run
powershell -NoProfile -Command "& '%~f0' 2>&1 | ForEach-Object { '' + $_ | Out-File -FilePath '%FAREVER_BUILD_LOG%' -Append -Encoding utf8; '' + $_ }; exit $LASTEXITCODE"

:tee_done
set "_RC=%ERRORLEVEL%"
if not defined FAREVER_BUILD_LOG goto :tee_exit

REM The header is all this file holds if the tee never ran, so a file that
REM did not grow past it means the build's own output was NOT captured --
REM say so rather than leaving a near-empty log looking like a record.
for %%A in ("%FAREVER_BUILD_LOG%") do set "_LOGSIZE=%%~zA"
if %_LOGSIZE% LEQ %_HDRSIZE% (
    echo.
    echo [WARN] The build transcript holds no build output ^(%%_LOGSIZE%% bytes^).
    echo        The run below was not recorded; read the console for it.
)
>> "%FAREVER_BUILD_LOG%" echo.
>> "%FAREVER_BUILD_LOG%" echo === build-mod finished with exit code %_RC% ===
if "%_RC%"=="0" goto :tee_exit
echo.
echo [FAIL] Build did not finish. Full transcript: %FAREVER_BUILD_LOG%
:tee_exit
exit /b %_RC%


:run_build
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

REM -- Preflight: ensure the Rust toolchain is present and active --
REM Two distinct failures: rustc missing because no default toolchain is set
REM (rustup IS installed -> fixable here), vs. Rust not installed at all
REM (neither rustc nor rustup on PATH -> must be installed by the user).
REM The old check ran 'rustup default stable' unconditionally, so a machine with
REM no rustup printed "'rustup' is not recognized..." and fell through to the
REM generic red banner instead of naming the real problem. Both probes are run
REM with the output discarded here so the script, not rustup, owns the message.
REM This preflight (and the MSVC / SDK ones below) COLLECTS failures instead of
REM aborting on the first one, so a half-configured machine is told about every
REM missing tool in a single run; :preflight_ok skips the summary when none is.
set "_MISS_RUST="
set "_MISS_MSVC="
set "_MISS_SDK="
REM Flattened with a skip label rather than nested if/else: the "no rustup" and
REM "rustup default stable failed" arms must NOT fall through into the arm below
REM them now that they record a failure instead of aborting the whole script.
rustc --version >nul 2>&1
if not errorlevel 1 goto :rust_preflight_done
where rustup >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Rust is not installed - neither 'rustc' nor 'rustup' was found on PATH.
    echo The native memory reader ^(farever_native^) cannot be built without it.
    echo Install the Rust toolchain, then re-run build-mod.bat:
    echo   winget install --id Rustlang.Rustup -e --accept-package-agreements --accept-source-agreements
    echo   or download from https://rustup.rs
    echo After installing, close and reopen the terminal so PATH picks up %USERPROFILE%\.cargo\bin.
    set "_MISS_RUST=Rust is not installed - no rustc or rustup on PATH"
    goto :rust_preflight_done
)
echo Rust toolchain present but no default set - running 'rustup default stable' ...
rustup default stable
if errorlevel 1 (
    echo.
    echo ERROR: 'rustup default stable' failed - the stable toolchain could not be installed/activated.
    echo Run it manually to see the reason: rustup default stable
    echo If it cannot reach the network, verify https://static.rust-lang.org is reachable.
    set "_MISS_RUST=rustup is installed but 'rustup default stable' failed"
    goto :rust_preflight_done
)
rustc --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: rustup reported success but 'rustc' is still not on PATH.
    echo Close and reopen the terminal ^(so PATH picks up %USERPROFILE%\.cargo\bin^) and re-run build-mod.bat.
    set "_MISS_RUST=rustup succeeded but rustc is still not on PATH"
)
:rust_preflight_done
REM -- Preflight: ensure MSVC linker is on PATH (Git's /usr/bin/link.exe shadows MSVC) --
where cl.exe >nul 2>&1
if errorlevel 1 (
    if exist "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" (
        call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
    ) else if exist "C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" (
        call "C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
    ) else if exist "%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" (
        for /f "usebackq delims=" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2^>nul`) do (
            if exist "%%i\VC\Auxiliary\Build\vcvars64.bat" call "%%i\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
        )
    ) else if exist "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe" (
        for /f "usebackq delims=" %%i in (`"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2^>nul`) do (
            if exist "%%i\VC\Auxiliary\Build\vcvars64.bat" call "%%i\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1
        )
    )
    where cl.exe >nul 2>&1
    if errorlevel 1 (
        echo.
        echo ERROR: Visual Studio C++ build tools not found.
        echo Install 'Desktop development with C++' or 'Build Tools for Visual Studio 2022' ^(VC++ toolchain^) and re-run.
        echo See: https://visualstudio.microsoft.com/visual-cpp-build-tools/
        set "_MISS_MSVC=no cl.exe / vcvars64.bat found on PATH"
    )
)
REM -- Preflight: ensure Windows SDK is installed (kernel32.lib) --
set "_HAS_SDK="
if defined WindowsSdkDir if exist "%WindowsSdkDir%Lib\%WindowsSDKVersion%um\x64\kernel32.lib" set "_HAS_SDK=1"
if not defined _HAS_SDK if exist "C:\Program Files (x86)\Windows Kits\10\Lib" (
    for /d %%D in ("C:\Program Files (x86)\Windows Kits\10\Lib\10.*") do if exist "%%D\um\x64\kernel32.lib" set "_HAS_SDK=1"
)
if not defined _HAS_SDK (
    echo.
    echo ERROR: Windows 10/11 SDK not found - kernel32.lib is missing.
    echo The C++ Build Tools are installed but the Windows SDK is not.
    echo Fix one of these, then re-run build-mod.bat:
    echo   Option A ^(recommended^): Visual Studio Installer ^> Modify ^> "Build Tools" ^> Individual components ^> check "Windows 11 SDK ^(10.0.22621^)" or "Windows 10 SDK"
    echo   Option B: winget install --id Microsoft.WindowsSDK.10.0.22621 --accept-package-agreements --accept-source-agreements
    echo   Option C: https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/
    echo.
    echo After installing, close and reopen the terminal so vcvars picks up the new SDK.
    set "_MISS_SDK=kernel32.lib not found under Windows Kits"
)
set "_HAS_SDK="

REM -- Preflight summary: report every missing toolchain at once, then stop --
REM Deliberately goto-based, not one parenthesized block: a block expands every
REM %VAR% as it is parsed, so the flags set above would read back empty.
set "_MISSING="
if defined _MISS_RUST set "_MISSING=1"
if defined _MISS_MSVC set "_MISSING=1"
if defined _MISS_SDK set "_MISSING=1"
if not defined _MISSING goto :preflight_ok
echo.
echo ------------------------------------------------------------------
echo TOOLCHAIN PREFLIGHT FAILED - the build cannot start:
if defined _MISS_RUST echo   * Rust        : %_MISS_RUST%
if defined _MISS_MSVC echo   * MSVC        : %_MISS_MSVC%
if defined _MISS_SDK  echo   * Windows SDK : %_MISS_SDK%
echo.
echo Fix each item above, then re-run build-mod.bat.
echo Tip: build_tools\doctor.bat prints a full environment report.
goto err
:preflight_ok

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

:: The compiler's contract: a rebuild over unchanged data is byte-identical,
:: so git status must come back clean for the shims. Dirt here means the exe
:: version-stamped with the current git rev would carry data that rev does not
:: describe. [5/5] re-checks this (fail-closed), this early check just stops
:: before the expensive PyInstaller step.
.venv\Scripts\python.exe build_tools\shim_contract.py
if errorlevel 1 (
    echo.
    echo See above: commit the shims with the data that produced them, or
    echo restore them. Set FAREVER_ALLOW_DIRTY_SHIMS=1 to build a test exe anyway.
    goto err
)

:: Report (never fail) the rows the sheets left unnamed: a row whose name is
:: still its own id prints as 'Back_RBee_Wiz' in the app, so this is where a
:: fresh scan's remaining gaps show up. Add --fail-on-gear to gate on it.
.venv\Scripts\python.exe build_tools\check_unnamed_items.py

:: Warn (never fail) when a compiled shim is older than the sheet it was built
:: from. The compiler SKIPS a shim whose sheet is missing or whose payload is
:: empty, leaving the old committed file in place - which the size gate below
:: then accepts, so the exe would ship a list older than the data. This is the
:: last point before packaging at which that can still be seen.
.venv\Scripts\python.exe build_tools\shim_freshness.py

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
