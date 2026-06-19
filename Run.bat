@echo off
setlocal

:run
call ".\.venv\Scripts\python.exe" run.py
echo(
set /p dummy=Press Enter to run again, or close the window to stop: 
goto run