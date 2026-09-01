@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo  FareverPal Combat Hook: Hot Injector
echo =======================================================

set "SCRIPT_DIR=%~dp0"
set "SRC_DIR=%SCRIPT_DIR%..\..\GameFiles\Farever\hooks\"
set "GCC_BIN=%SRC_DIR%w64devkit\bin"

:: Automatically recompile fresh DLL and EXE if compiler is available
if exist "%GCC_BIN%\gcc.exe" (
    echo [1/2] Compiling fresh farever_dps.dll ^& injector.c with bundled GCC...
    set "PATH=%GCC_BIN%;!PATH!"
    pushd "%SRC_DIR%hook\farever_dps"
    gcc -O2 -shared -o farever_dps.dll farever_dps.c ..\minhook\buffer.c ..\minhook\trampoline.c ..\minhook\hook.c ..\minhook\hde\hde64.c -lws2_32
    if !ERRORLEVEL! equ 0 (
        if exist "%SCRIPT_DIR%farever_dps.dll" (
            del "%SCRIPT_DIR%farever_dps.dll.old" 2>nul
            ren "%SCRIPT_DIR%farever_dps.dll" farever_dps.dll.old 2>nul
        )
        copy /y farever_dps.dll "%SCRIPT_DIR%" >nul 2>&1
        echo [OK] farever_dps.dll compiled and copied successfully.
    ) else (
        echo [WARN] Compilation of farever_dps.dll failed.
    )
    gcc -O2 -o farever_dps.exe injector.c
    if !ERRORLEVEL! equ 0 (
        if exist "%SCRIPT_DIR%farever_dps.exe" (
            del "%SCRIPT_DIR%farever_dps.exe.old" 2>nul
            ren "%SCRIPT_DIR%farever_dps.exe" farever_dps.exe.old 2>nul
        )
        copy /y farever_dps.exe "%SCRIPT_DIR%" >nul 2>&1
        copy /y farever_dps.exe "%SCRIPT_DIR%farever_uninject.exe" >nul 2>&1
        echo [OK] farever_dps.exe compiled and copied successfully.
    ) else (
        echo [WARN] Compilation of farever_dps.exe failed.
    )
    popd
) else (
    echo [INFO] Bundled GCC not found, using pre-built binaries...
)

echo.
echo [2/2] Running Hot Injector...
cd /d "%SCRIPT_DIR%"

if exist "farever_dps.exe" (
    farever_dps.exe
) else if exist "%SRC_DIR%hook\farever_dps\farever_dps.exe" (
    "%SRC_DIR%hook\farever_dps\farever_dps.exe"
) else (
    echo [ERROR] farever_dps.exe not found!
)

echo.
echo =======================================================
echo Injection complete. Check monitor_bridge.bat for UDP output.
echo =======================================================
pause
