#!/bin/bash

APP_DIR="/home/site/wwwroot"
echo "Starting app from: $APP_DIR"
cd $APP_DIR

export PYTHONPATH=$APP_DIR:$PYTHONPATH

echo "Verifying and installing dependencies..."
python3 -m pip install --upgrade pip -q
python3 -m pip install -r requirements.txt -q

# OS libraries FIRST, then browser binary
python3 -m playwright install-deps chromium
python3 -m playwright install chromium

echo "Dependencies verified."

if [ ! -d "app" ]; then
    echo "ERROR: app directory not found in $APP_DIR"
    ls -la
    exit 1
fi

echo "Starting gunicorn with 1 worker..."
python3 -m gunicorn app.api.main:app \
    --workers 1 \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:${PORT:-8000} \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -