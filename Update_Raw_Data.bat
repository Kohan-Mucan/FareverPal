@echo off
setlocal
echo Updating game data sheets...
call ".\.venv\Scripts\python.exe" compiler.py
echo(
echo Data update complete.
pause
