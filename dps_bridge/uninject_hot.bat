@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo  FareverPal Combat Hook: Hot Uninjector
echo =======================================================

set "SCRIPT_DIR=%~dp0"
set "SRC_DIR=%SCRIPT_DIR%..\..\GameFiles\Farever\hooks\"
set "GCC_BIN=%SRC_DIR%w64devkit\bin"

:: Automatically recompile fresh injector if compiler is available
if exist "%GCC_BIN%\gcc.exe" (
    echo [1/2] Compiling fresh injector with bundled GCC...
    set "PATH=%GCC_BIN%;!PATH!"
    pushd "%SRC_DIR%hook\farever_dps"
    rem safe_uninject_client.h (SafeUnload-aware uninjector) lives here with
    rem injector.c, which #includes it. Build outputs stay in this source folder.
    if not exist "safe_uninject_client.h" (
        echo [FAIL] hook\farever_dps\safe_uninject_client.h is missing.
        echo        injector.c includes it, so the uninjector cannot be rebuilt;
        echo        uninjecting with an older binary could tear the DLL out live.
        echo        Restore the file and rerun.
        popd
        pause
        exit /b 1
    )
    gcc -O2 -o farever_dps.exe injector.c
    if !ERRORLEVEL! equ 0 (
        echo [OK] Injector compiled in "%SRC_DIR%hook\farever_dps".
    ) else (
        echo [WARN] Compilation of injector failed, using existing binary.
    )
    popd
) else (
    echo [INFO] Bundled GCC not found, using pre-built binaries...
)

echo.
echo [2/2] Uninjecting farever_dps.dll from running game...
cd /d "%SCRIPT_DIR%"

if exist "%SRC_DIR%hook\farever_dps\farever_dps.exe" (
    "%SRC_DIR%hook\farever_dps\farever_dps.exe" --uninject
) else (
    echo [ERROR] farever_dps.exe not found!
)

echo.
echo =======================================================
echo Uninject complete. The game continues running normally.
echo =======================================================
pause
exit /b 0
