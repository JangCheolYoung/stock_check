#!/usr/bin/env bash
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/stock_check}

cd "$APP_DIR"
source .venv/bin/activate
export PYTHONPATH="$APP_DIR"
python -m stock_check.app.web_admin
