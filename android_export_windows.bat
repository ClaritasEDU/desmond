@echo off
REM Desmond - Android SMS Exporter for Windows
REM Exports your SMS/MMS from Android backup files to AI-ready formats

echo.
echo ============================================================
echo   Desmond - Android SMS Exporter
echo   "We have to push the button."
echo ============================================================
echo.

REM Check if Python is installed
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python is not installed or not in PATH.
    echo.
    echo Please install Python from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

REM Get the directory of this batch file
set "SCRIPT_DIR=%~dp0"

REM Run the exporter with all arguments passed through
python "%SCRIPT_DIR%android_sms_exporter.py" %*

echo.
pause
