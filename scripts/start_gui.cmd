@echo off
set "SYNC_PROJECT=%~dp0.."
if not exist "%SYNC_PROJECT%\.venv\Scripts\python.exe" (
    echo Python environment is missing. Follow README setup instructions first.
    pause
    exit /b 1
)
"%SYNC_PROJECT%\.venv\Scripts\python.exe" -c "import customtkinter, rekordbox_sync" >nul 2>&1
if errorlevel 1 (
    echo GUI dependencies are missing. Install the gui extra described in README.
    pause
    exit /b 1
)
start "" /min powershell.exe -NoProfile -WindowStyle Hidden -File "%SYNC_PROJECT%\scripts\launch_gui.ps1"
