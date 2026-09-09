@echo off
setlocal
rem Developer launcher: run the app with the Top DPS BOARD TRACE on.
rem
rem Live 2026-10-04, in a dungeon: "top dps is still resetting after every time
rem i leave combat" ... and NO `Top DPS: reset (...)` line appears. That rules
rem out a tracker wipe - what cleared is the BOARD the overlay DISPLAYS, which
rem nothing logged. With FAREVER_DPS_TRACE=1 the tracker writes one JSONL line
rem per board change (switch / clear) with the whole decision state beside it -
rem the boss/adds/trash/overall totals, the instance gate, in_boss_fight and the
rem game's combat flag - and echoes a one-line summary to the Activity Log.
rem
rem Play ONE dungeon, then close the app and read:
rem   <moddata>\dps\dps_trace.jsonl      (dist\moddata or dist\dev-moddata)
rem The Activity Log carries the same events live if you want to watch while you
rem play. It is a diagnostic, not a record - delete the file when you are done.
set "FAREVER_DPS_TRACE=1"
cd /d "%~dp0..\.."

:run
call ".\.venv\Scripts\python.exe" run.py
echo(
set /p dummy=Press Enter to run again, or close the window to stop: 
goto run
