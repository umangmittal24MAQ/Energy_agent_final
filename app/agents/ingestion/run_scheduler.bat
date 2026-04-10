@echo off
REM Ingestion Pipeline Automation Runner
REM This batch file starts the Python ingestion orchestrator

setlocal enabledelayedexpansion

REM Get the directory where this batch file is located
set SCRIPT_DIR=%~dp0

REM Change to the script directory
cd /d "%SCRIPT_DIR%"

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    pause
    exit /b 1
)

REM Install required dependencies if needed
echo Checking dependencies...
python -m pip install schedule --quiet 2>nul

REM Run the orchestrator
echo.
echo Starting Ingestion Pipeline Scheduler...
echo.
python ingestion_orchestrator.py

pause
