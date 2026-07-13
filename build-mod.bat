@echo off
setlocal
cd /d %~dp0

call build.bat
if errorlevel 1 goto err

echo.
echo [2/3] Packaging executable...
.venv\Scripts\python.exe -m PyInstaller --noconfirm FareverPal.spec
if errorlevel 1 goto err

echo.
echo [3/3] Validating Build Integrity...
set "VERIFY_SCRIPT=packaging\verify_assets.py"
if not exist "%VERIFY_SCRIPT%" set "VERIFY_SCRIPT=..\htdocs\tools\Verification Script.py"

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
    echo If it built, the exe is in dist\FareverPal.exe
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
