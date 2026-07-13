@echo off
setlocal
echo ============================================================
echo  FareverPal Companion - Test Runner
echo ============================================================
echo.

:: Check if pytest is installed
python -m pytest --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo ERROR: pytest not found. Please install it with:
    echo pip install pytest
    pause
    exit /b 1
)

echo Running all tests in tests/ directory...
echo.

:: Run pytest
python -m pytest tests/

if %ERRORLEVEL% equ 0 (
    echo.
    echo ============================================================
    echo  SUCCESS: All tests passed!
    echo ============================================================
) else (
    echo.
    echo ============================================================
    echo  FAILURE: Some tests failed. Check the output above.
    echo ============================================================
)

pause
endlocal
