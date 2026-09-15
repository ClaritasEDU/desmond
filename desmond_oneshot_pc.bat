@echo off
REM ==========================================================================
REM  DESMOND - ONE-SHOT (PC / Windows)
REM  Double-click this file to export your WHOLE iMessage/SMS history from an
REM  iPhone backup on this PC into browsable, AI-ready files (text + summary).
REM
REM  This is a launcher only. The actual work is done by
REM  imessage_exporter_windows.py, which reads your backup READ-ONLY.
REM
REM  REQUIREMENT: an UNENCRYPTED iPhone backup must already exist on this PC.
REM  Make one first: connect iPhone > iTunes (Win 10) or Apple Devices (Win 11)
REM  > select device > "Back Up Now" > make sure "Encrypt local backup" is OFF.
REM ==========================================================================
setlocal

echo.
echo ==============================================================
echo   DESMOND - ONE-SHOT (PC / Windows)
echo   Exporting your whole iMessage/SMS history from an iPhone backup.
echo ==============================================================
echo.

REM --- Run from the folder this file lives in (dummy-proof) ------------------
set "SCRIPT_DIR=%~dp0"

REM --- Check Python is installed --------------------------------------------
where python >nul 2>nul
if %ERRORLEVEL% neq 0 (
    echo ERROR: Python is not installed or not on your PATH.
    echo.
    echo 1^) Install Python from https://www.python.org/downloads/
    echo 2^) During install, CHECK the box "Add Python to PATH"
    echo 3^) Then double-click this file again.
    echo.
    pause
    exit /b 1
)

REM --- Logging: write a timestamped log AND show output on screen ------------
REM  Uses PowerShell Tee-Object so you see progress live and keep a full log.
set "LOG_DIR=%USERPROFILE%\Documents\Desmond_Logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>nul
for /f "tokens=1-6 delims=/:. " %%a in ("%date% %time%") do set "STAMP=%%c%%a%%b_%%d%%e%%f"
set "STAMP=%STAMP: =0%"
set "LOG_FILE=%LOG_DIR%\oneshot_pc_%STAMP%.log"

echo Launcher log: %LOG_FILE%
echo Running full export of all messages...
echo.

REM --full = export the complete history in one shot.
powershell -NoProfile -ExecutionPolicy Bypass -Command "& python '%SCRIPT_DIR%imessage_exporter_windows.py' --full 2>&1 | Tee-Object -FilePath '%LOG_FILE%'"
set "STATUS=%ERRORLEVEL%"

echo.
if "%STATUS%"=="0" (
    echo ==============================================================
    echo   DONE. Your export is in:  %USERPROFILE%\Documents\iMessages_Export
    echo   Open SUMMARY.md in that folder to see what was exported.
    echo   Full log saved to: %LOG_FILE%
    echo ==============================================================
) else (
    echo ==============================================================
    echo   Something went wrong ^(exit code %STATUS%^).
    echo   Most common causes:
    echo     - No iPhone backup on this PC yet ^(make one, unencrypted^).
    echo     - The backup is ENCRYPTED ^(turn off "Encrypt local backup"
    echo       and make a fresh backup^).
    echo   Details are in the log: %LOG_FILE%
    echo ==============================================================
)

echo.
pause
exit /b %STATUS%
