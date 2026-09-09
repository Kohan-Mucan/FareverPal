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
    rem Crash-safe unload export (SafeUnload / IsSafeUnloadReady): unity-include
    rem APPENDED to farever_dps.c so it can reach the bridge's file-scope
    rem statics (g_stop_event / g_worker_thread). See the header of
    rem GameFiles\Farever\hooks\hook\farever_dps\safe_unload.c.
    rem -Wl,--export-all-symbols keeps the original DLL's full export surface
    rem (MH_* + MinHook internals).
    rem safe_unload.c and safe_uninject_client.h live HERE, with the source
    rem they belong to (injector.c #includes the header). Build outputs stay
    rem here; do not copy native binaries into the app tree.
    if not exist "safe_unload.c" (
        echo [FAIL] hook\farever_dps\safe_unload.c is missing.
        echo        It defines the SafeUnload / IsSafeUnloadReady exports;
        echo        without it uninject needs a game restart. Restore it and rebuild.
        popd
        pause
        exit /b 1
    )
    if not exist "safe_uninject_client.h" (
        echo [FAIL] hook\farever_dps\safe_uninject_client.h is missing.
        echo        injector.c includes it, so both the injector and the
        echo        uninjector cannot be built. Restore it and rebuild.
        popd
        pause
        exit /b 1
    )
    copy /b farever_dps.c + safe_unload.c farever_dps_full.c >nul 2>&1
    gcc -O2 -shared -o farever_dps.dll farever_dps_full.c ..\minhook\buffer.c ..\minhook\trampoline.c ..\minhook\hook.c ..\minhook\hde\hde64.c -lws2_32 -Wl,--export-all-symbols
    if !ERRORLEVEL! equ 0 (
        echo [OK] farever_dps.dll compiled in "%SRC_DIR%hook\farever_dps".
    ) else (
        echo [WARN] Compilation of farever_dps.dll failed.
    )
    gcc -O2 -o farever_dps.exe injector.c
    if !ERRORLEVEL! equ 0 (
        echo [OK] farever_dps.exe compiled in "%SRC_DIR%hook\farever_dps".
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

if exist "%SRC_DIR%hook\farever_dps\farever_dps.exe" (
    "%SRC_DIR%hook\farever_dps\farever_dps.exe"
) else (
    echo [ERROR] farever_dps.exe not found!
)

echo.
echo =======================================================
echo Injection complete. Check monitor_bridge.bat for UDP output.
echo =======================================================
pause
exit /b 0
