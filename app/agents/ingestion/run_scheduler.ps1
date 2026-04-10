# Ingestion Pipeline Automation - PowerShell Runner

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Change to script directory
Set-Location $ScriptDir

# Check Python availability
try {
    python --version | Out-Null
} catch {
    Write-Host "Error: Python is not installed or not in PATH" -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Install required dependencies
Write-Host "Checking dependencies..." -ForegroundColor Cyan
python -m pip install schedule --quiet 2>$null

# Run the orchestrator
Write-Host ""
Write-Host "Starting Ingestion Pipeline Scheduler..." -ForegroundColor Green
Write-Host ""

python ingestion_orchestrator.py

Read-Host "Press Enter to exit"
