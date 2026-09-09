@echo off
setlocal
cd /d %~dp0
rem Developer launcher: keep console output visible and capture crash traces in run.log.
rem
rem Two logs, two folders. run.log (the Python/native crash trace) stays at the
rem repo root. bridge_dev.log (what the C bridge binaries write while the game
rem runs) goes to dps_bridge\, next to the binaries it describes - which is why
rem the build transcript beside it is called bridge_build.log, so a build never
rem truncates the log you are reading. The two used to share one variable,
rem which meant moving the bridge log also moved run.log.
set "FAREVER_DEV_CRASH_LOG=%~dp0run.log"
set "FAREVER_BRIDGE_DEV_LOG_DIR=%~dp0dps_bridge"

:run
call ".\.venv\Scripts\python.exe" run.py
echo(
set /p dummy=Press Enter to run again, or close the window to stop: 
goto run
