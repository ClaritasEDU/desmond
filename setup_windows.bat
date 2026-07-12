@echo off
REM Desmond - Windows Setup Script
REM Sets up automatic hourly exports using Windows Task Scheduler

echo.
echo ============================================================
echo   Desmond - Windows Setup
echo   Configuring automatic iMessage exports
echo ============================================================
echo.

REM Check for admin rights (needed for Task Scheduler)
net session >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo This script requires administrator privileges to create scheduled tasks.
    echo Please right-click and select "Run as administrator"
    echo.
    pause
    exit /b 1
)

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

REM Get Python path (first match = what `python` itself resolves to;
REM the LAST match is often the useless WindowsApps store alias)
for /f "delims=" %%i in ('where python') do if not defined PYTHON_PATH set "PYTHON_PATH=%%i"

REM Get the directory of this batch file
set "SCRIPT_DIR=%~dp0"
set "EXPORTER_PATH=%SCRIPT_DIR%imessage_exporter_windows.py"

echo Python found at: %PYTHON_PATH%
echo Exporter found at: %EXPORTER_PATH%
echo.

REM Create the scheduled task
echo Creating scheduled task for hourly exports...

schtasks /create /tn "Desmond iMessage Exporter" /tr "\"%PYTHON_PATH%\" \"%EXPORTER_PATH%\"" /sc hourly /mo 1 /f

if %ERRORLEVEL% equ 0 (
    echo.
    echo SUCCESS: Scheduled task created!
    echo.
    echo The exporter will now run automatically every hour.
    echo Exports will be saved to: %USERPROFILE%\Documents\iMessages_Export
    echo.
    echo To manage the task:
    echo   - Open Task Scheduler (taskschd.msc)
    echo   - Look for "Desmond iMessage Exporter"
    echo.
    echo To remove the scheduled task:
    echo   schtasks /delete /tn "Desmond iMessage Exporter" /f
    echo.
) else (
    echo.
    echo ERROR: Failed to create scheduled task.
    echo Please check your permissions and try again.
    echo.
)

REM Run initial export
echo.
echo Running initial export now...
echo.

python "%EXPORTER_PATH%" --full

echo.
echo ============================================================
echo   Setup complete!
echo ============================================================
echo.
pause
