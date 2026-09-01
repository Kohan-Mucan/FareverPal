@echo off
echo Building FareverPal DPS Bridge Components...

echo [1/2] Building dinput8.dll ^& version.dll proxies...
if exist build_proxy.bat call build_proxy.bat

echo [2/2] Building farever_dps.dll injector...
if exist build_dll.bat call build_dll.bat

echo Done!
