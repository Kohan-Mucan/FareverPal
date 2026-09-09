@echo off
REM --- Colour: red = failed, green = built/updated, orange = unchanged ------
REM A build log is read in one pass, and its three answers are different in
REM kind: a red line is a step that did NOT do its job, a green line is one that
REM did, and an orange line is one that correctly decided there was nothing to
REM do - the /noverify notice, the "already current" notices, the pre-flight
REM warning about a game still holding the DLL. As plain text all three read
REM like the same kind of sentence, and the one that matters is the one you look
REM for in a hurry.
REM
REM The ESC byte is DERIVED at run time, never typed into the file: an invisible
REM 0x1B survives no editor round-trip and shows up as nothing in a diff, so the
REM byte is rebuilt from cmd's own prompt expansion on every run instead.
REM
REM NO_COLOR=1 (any value) makes each of these an empty string, leaving the
REM output byte-for-byte what it was before - for a log that gets piped, pasted
REM into an issue, or read by a program.
REM One `set` per name, never a `&`-chained list: in `set "A=&set "B="` the
REM quotes do not close where you expect, so the whole chain becomes ONE
REM value for A and B..F are never set at all - which looked like colour simply
REM not working under NO_COLOR and printed the chain into the log.
set "C_RESET="
set "C_OK="
set "C_WARN="
set "C_FAIL="
set "C_STEP="
set "C_HEAD="
if not defined NO_COLOR (
    for /f "delims=" %%E in ('echo prompt $E^| cmd') do set "ESC=%%E"
    set "C_RESET=!ESC![0m"
    set "C_OK=!ESC![92m"
    set "C_WARN=!ESC![33m"
    set "C_FAIL=!ESC![91m"
    set "C_STEP=!ESC![96m"
    set "C_HEAD=!ESC![1;96m"
)

echo %C_HEAD%=======================================================%C_RESET%
echo  FareverPal DPS Bridge: Build All Components
echo %C_HEAD%=======================================================%C_RESET%
echo.
echo Builds the active bridge binaries (farever_dps.dll + injector, dinput8.dll
echo and userenv.dll) with the bundled w64devkit GCC. The retired version.dll
echo is not built or copied; the selected outputs go to this folder, which is
echo the only place they are published to.
echo.

set "SCRIPT_DIR=%~dp0"

REM Defined before anything can fail, so the :failed branch below always has a
REM valid path to append its verdict to.
REM
REM bridge_build.log, NOT bridge_dev.log: run-log.bat points the app's dev
REM logging at THIS folder, so bridge_dev.log is the live log the C bridge
REM binaries append to while you play. A build that truncated it would wipe
REM the session you were reading. The two are unrelated files that merely
REM share a folder.
set "BUILD_LOG=%SCRIPT_DIR%bridge_build.log"

REM The real build logic lives with the C sources in GameFiles (this folder
REM has none). Called with /nopause: it prints the per-step progress and its
REM own failure lines, but the ending -- the banner and the single wait for a
REM key -- belongs to the script the user actually launched, so one build
REM never ends twice. Its exit code is OURS: a failed compile must not end
REM with a green "Build finished" and exit 0.
REM
REM %* forwards this script's own arguments to the native builder, so ITS flags
REM are reachable from here too: /noverify skips the ~12s manifest proof for a
REM build that only changes C code, and says loudly that the result is
REM UNVERIFIED. Before this, anything after /nopause was silently discarded --
REM the flag lived on the far side of a wrapper that dropped it, which is the
REM quietest way for a documented option to not exist.
REM
REM   build_all.bat [/noverify]
set "HOOKS_BUILD=%SCRIPT_DIR%..\..\GameFiles\Farever\hooks\build_all.bat"
if not exist "%HOOKS_BUILD%" (
    echo %C_FAIL%[ERROR] Native hooks builder not found:%C_RESET%
    echo   %HOOKS_BUILD%
    goto :failed
)

REM Full transcript of the native build -> bridge_build.log in THIS folder.
REM A failed build used to leave nothing behind but a console window the
REM user then closed, taking every gcc diagnostic with it; the log is what
REM makes the failure readable afterwards. Written on every run, not only on
REM failure: the tail of a green build is the quickest way to see which step
REM rebuilt which binary.
REM
REM This is the BUILD log. It is not the app's runtime bridge log, which
REM core/bridge_log.py writes to this same folder as bridge_dev.log.
> "%BUILD_LOG%" echo === FareverPal DPS bridge build - %DATE% %TIME% ===
>>"%BUILD_LOG%" echo === builder: %HOOKS_BUILD%
>>"%BUILD_LOG%" echo.

REM Never advertise a transcript that does not exist: a path to a file that
REM was never written reads as "logging is working" when nothing was kept.
if exist "%BUILD_LOG%" (
    for %%A in ("%BUILD_LOG%") do set "_HDRSIZE=%%~zA"
) else (
    echo %C_WARN%[WARN] Could not create "%BUILD_LOG%" - no transcript will be kept.%C_RESET%
    set "BUILD_LOG="
)
echo.

REM Tee with PowerShell so the output still streams to the console as it is
REM produced (a plain > redirect would swallow it until the very end, and a
REM for/f pipe would eat blank lines and mangle & | ^ in gcc's diagnostics).
REM Two details this form gets right and Tee-Object does not:
REM   - Tee-Object writes UTF-16 in Windows PowerShell, which renders as a
REM     file of spaced-out NULs in any normal editor. Out-File -Encoding utf8
REM     writes plain text.
REM   - '' + $_ forces the stream item to a string first, so gcc's stderr does
REM     not reach the log wrapped in PowerShell's NativeCommandError framing
REM     (At line:1 char:1 / CategoryInfo / FullyQualifiedErrorId).
REM If PowerShell is somehow unavailable, still run the build - a missing log
REM must never be what breaks the build.
where powershell.exe >nul 2>&1
if errorlevel 1 goto :tee_plain
if not defined BUILD_LOG goto :tee_plain
powershell -NoProfile -Command "& '%HOOKS_BUILD%' /nopause %* 2>&1 | ForEach-Object { '' + $_ | Out-File -FilePath '%BUILD_LOG%' -Append -Encoding utf8; '' + $_ }; exit $LASTEXITCODE"
goto :tee_done

:tee_plain
if not defined BUILD_LOG (
    echo %C_WARN%[WARN] No build transcript will be written.%C_RESET%
) else (
    echo %C_WARN%[WARN] powershell.exe not found - no build transcript will be written.%C_RESET%
)
call "%HOOKS_BUILD%" /nopause %*

:tee_done
REM The header is all this file holds if the tee never ran, so a file that
REM did not grow past it means the native build's output was NOT captured.
if defined BUILD_LOG for %%A in ("%BUILD_LOG%") do set "_LOGSIZE=%%~zA"
if defined BUILD_LOG if %_LOGSIZE% LEQ %_HDRSIZE% (
    echo.
    echo %C_WARN%[WARN] The build transcript holds no build output ^(%_LOGSIZE% bytes^).
    echo        The native build was not recorded; read the console for it.%C_RESET%
)
if errorlevel 1 goto :failed
goto :done

:done
echo.
echo %C_HEAD%=======================================================%C_RESET%
echo %C_OK%Build finished. Active binaries were copied to:%C_RESET%
echo   %SCRIPT_DIR%
if defined BUILD_LOG echo   Build transcript: %BUILD_LOG%
echo %C_HEAD%=======================================================%C_RESET%
pause
exit /b 0

:failed
REM Record the verdict in the log too: the transcript on its own ends at the
REM last gcc error, with nothing saying the run as a whole did not finish.
if defined BUILD_LOG (
    >> "%BUILD_LOG%" echo.
    >> "%BUILD_LOG%" echo [FAIL] The build did NOT finish. See the errors above.
)
echo.
echo %C_HEAD%=======================================================%C_RESET%
echo %C_FAIL%[FAIL] The build did NOT finish. See the errors above.%C_RESET%
if defined BUILD_LOG echo        Full transcript saved to: %BUILD_LOG%
echo %C_HEAD%=======================================================%C_RESET%
pause
exit /b 1
