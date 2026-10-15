@echo off
title TITAN
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Titan is not set up yet. Open VS Code and run:  python setup_titan.py
    pause
    exit /b 1
)
echo Starting Titan... your browser will open in a moment.
echo Say "Titan" to speak to the assistant. Keep this window open while using Titan.
".venv\Scripts\python.exe" dashboard\server.py --wake-word
if errorlevel 1 pause
