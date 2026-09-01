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
    gcc -O2 -o farever_dps.exe injector.c
    if !ERRORLEVEL! equ 0 (
        if exist "%SCRIPT_DIR%farever_dps.exe" (
            del "%SCRIPT_DIR%farever_dps.exe.old" 2>nul
            ren "%SCRIPT_DIR%farever_dps.exe" farever_dps.exe.old 2>nul
        )
        copy /y farever_dps.exe "%SCRIPT_DIR%" >nul 2>&1
        copy /y farever_dps.exe "%SCRIPT_DIR%farever_uninject.exe" >nul 2>&1
        echo [OK] Injector compiled and updated.
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

if exist "farever_dps.exe" (
    farever_dps.exe --uninject
) else if exist "%SRC_DIR%hook\farever_dps\farever_dps.exe" (
    "%SRC_DIR%hook\farever_dps\farever_dps.exe" --uninject
) else (
    echo [ERROR] farever_dps.exe not found!
)

echo.
echo =======================================================
echo Uninject complete. The game continues running normally.
echo =======================================================
pause
