@echo off
setlocal
cd /d %~dp0

call build.bat
if errorlevel 1 goto err

echo.
echo [1.5/3] Compiling raw_data.py database...
.venv\Scripts\python.exe compiler.py
if errorlevel 1 goto err

echo.
echo [2/3] Packaging executable...

:: Extract Version-like info from the latest Git commit message
:: (e.g., if message is "v 0.3.0 Compass Fix", it extracts "v_0_3_0")
for /f "tokens=1,2" %%a in ('git log -1 --format^="%%s"') do (
    set "PART1=%%a"
    set "PART2=%%b"
)
if /i "%PART1%"=="v" (
    set "GIT_VER=V_%PART2:.=_%"
) else (
    set "GIT_VER=%PART1:.=_%"
)

:: Get short Git commit hash to uniquely identify the build
for /f "tokens=*" %%i in ('git rev-parse --short HEAD') do set GIT_REV=%%i

:: Define name (e.g., FareverPal-V_0_3_0-a1b2c3d)
set "EXE_NAME=FareverPal-%GIT_VER%-%GIT_REV%"

.venv\Scripts\python.exe -m PyInstaller --noconfirm FareverPal.spec
if errorlevel 1 goto err

:: Rename output to versioned name
if exist "dist\FareverPal.exe" (
    echo.
    echo Renaming dist\FareverPal.exe to dist\%EXE_NAME%.exe
    move /y "dist\FareverPal.exe" "dist\%EXE_NAME%.exe" >nul
)

echo.
echo [3/3] Validating Build Integrity...
set "VERIFY_SCRIPT=packaging\verify_assets.py"

.venv\Scripts\python.exe "%VERIFY_SCRIPT%"
if errorlevel 1 (
    echo.
    powershell -Command "Write-Host '--------------------------------------------------' -ForegroundColor Red"
    powershell -Command "Write-Host '   BUILD INCOMPLETE - MISSING CRITICAL DATA      ' -ForegroundColor Red -BackgroundColor Black"
    powershell -Command "Write-Host '--------------------------------------------------' -ForegroundColor Red"
) else (
    echo.
    powershell -Command "Write-Host '--------------------------------------------------' -ForegroundColor Green"
    powershell -Command "Write-Host '    100%% DONE - ALL ASSETS VERIFIED \o/           ' -ForegroundColor Green -BackgroundColor Black"
    powershell -Command "Write-Host '--------------------------------------------------' -ForegroundColor Green"
    echo If it built, the exe is in dist\%EXE_NAME%.exe
)

echo.
pause
goto :eof

:err
echo.
powershell -Command "Write-Host '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!' -ForegroundColor Red"
powershell -Command "Write-Host '             CRITICAL BUILD FAILURE               ' -ForegroundColor White -BackgroundColor Red"
powershell -Command "Write-Host '!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!' -ForegroundColor Red"
pause
exit /b 1
