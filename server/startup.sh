#!/bin/bash

APP_DIR="/home/site/wwwroot"
VENV_DIR="$APP_DIR/antenv"

echo "Starting app from: $APP_DIR"
cd $APP_DIR

export PYTHONPATH=$APP_DIR:$PYTHONPATH

# Force Playwright to save the browser in persistent storage
export PLAYWRIGHT_BROWSERS_PATH="/home/site/pw-browsers"

# --- 1. Create the antenv if it does not exist ---
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment 'antenv'..."
    python3 -m venv $VENV_DIR
fi

# --- 2. Activate the antenv ---
echo "Activating virtual environment..."
source $VENV_DIR/bin/activate

# --- 3. Install Dependencies ---
if [ ! -f "/home/site/.deps_installed" ]; then
    echo "Installing dependencies into antenv for the first time..."
    python3 -m pip install --upgrade pip -q
    python3 -m pip install -r requirements.txt -q

    touch /home/site/.deps_installed
    echo "Dependencies installed successfully."
else
    echo "Dependencies already installed, skipping pip install..."
fi

# --- 4. OS & Browser Dependencies ---
echo "Ensuring Playwright Chromium is in persistent storage..."
python3 -m playwright install chromium

echo "Installing missing Linux OS system libraries..."
# 🚀 THE FIX: This MUST run on every boot because Azure wipes the OS container!
python3 -m playwright install-deps chromium

# --- 5. Verify Application Exists ---
if [ ! -d "app" ]; then
    echo "ERROR: app directory not found in $APP_DIR"
    ls -la
    exit 1
fi

# --- 6. Boot the Server ---
echo "Starting gunicorn with 1 worker..."
python3 -m gunicorn app.api.main:app \
    --workers 1 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:${PORT:-8000} \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -