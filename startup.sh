#!/bin/bash

APP_DIR="/home/site/wwwroot"
echo "Starting app from: $APP_DIR"
cd $APP_DIR

# Add the app directory to PYTHONPATH so absolute imports work cleanly
export PYTHONPATH=$APP_DIR:$PYTHONPATH

echo "Verifying and installing dependencies..."
# Running pip install every time ensures Azure doesn't lose packages after a container restart.
# Pip will quickly skip packages that are already satisfied.
python3 -m pip install --upgrade pip --quiet
python3 -m pip install -r requirements.txt --quiet
python3 -m playwright install chromium
echo "Dependencies verified."


# Verify app module exists
if [ ! -d "app" ]; then
    echo "ERROR: app directory not found in $APP_DIR"
    ls -la
    exit 1
fi

echo "Starting gunicorn with 1 worker..."

# CRITICAL FIX: --workers 1 prevents 4 separate background schedulers from 
# spinning up and sending 4 duplicate emails or locking the SharePoint files.
python3 -m gunicorn app.api.main:app \
    --workers 1 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:${PORT:-8000} \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -