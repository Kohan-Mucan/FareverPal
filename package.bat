@echo off
setlocal
cd /d %~dp0

REM Build the one-file exe from the committed spec (FareverPal.spec) so the
REM exact bundle contents are version controlled and can't drift between
REM machines. The spec bundles this app's own assets,
REM collects the farever_native binary, and excludes unused Qt modules. Build
REM the Rust ext first (build.bat).

.venv\Scripts\python.exe -m PyInstaller --noconfirm FareverPal.spec

echo.
echo If it built, the exe is in dist\FareverPal.exe
