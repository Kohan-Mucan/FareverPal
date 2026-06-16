@echo off
setlocal
cd /d %~dp0

echo Building Rust memory reader (farever_native)...
call build.bat
if errorlevel 1 goto err

echo.
echo Packaging executable...
.venv\Scripts\python.exe -m PyInstaller --noconfirm FareverPal.spec

echo.
echo Complete!
echo If it built, the exe is in dist\FareverPal.exe
pause
goto :eof

:err
echo.
echo PACKAGE FAILED.
pause
exit /b 1
