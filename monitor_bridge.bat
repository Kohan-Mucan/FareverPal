@echo off
setlocal enabledelayedexpansion
title FareverPal Combat Bridge Live Monitor

cd /d "%~dp0"

echo ======================================================
echo Launching FareverPal Combat Bridge Live UDP Monitor...
echo ======================================================

set "PY_EXE="
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0.venv\Scripts\python.exe"
)

if not defined PY_EXE (
    where python.exe >nul 2>&1
    if !ERRORLEVEL! equ 0 set "PY_EXE=python.exe"
)

if not defined PY_EXE (
    where py.exe >nul 2>&1
    if !ERRORLEVEL! equ 0 set "PY_EXE=py.exe"
)

if not defined PY_EXE (
    echo [ERROR] Python not found.
    pause
    exit /b 1
)

echo [OK] Using Python: !PY_EXE!
echo.
"!PY_EXE!" "%~dp0monitor_bridge.py"
pause
