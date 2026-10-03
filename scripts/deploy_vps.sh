#!/usr/bin/env bash
# Runs on the VPS (as the CloudPanel site user) after CI has checked out the
# commit to deploy: installs dependencies, restarts the app and waits until
# /health reports MongoDB reachable. Exits non-zero (failing the CI job) if
# the app does not come back.
#
# Expects, in ~/leadai: .venv/, .env and start.sh (which launches uvicorn on
# 127.0.0.1:8090 and is also run by cron to restart the app if it stops).
set -euo pipefail

APP_DIR="$HOME/leadai"
HEALTH_URL="http://127.0.0.1:8090/health"
cd "$APP_DIR"

echo "==> Installing dependencies"
.venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt

echo "==> Stopping the running app"
pkill -u "$USER" -f "uvicorn app.main:app" || true
for _ in $(seq 1 20); do
  pgrep -u "$USER" -f "uvicorn app.main:app" >/dev/null || break
  sleep 1
done
pkill -9 -u "$USER" -f "uvicorn app.main:app" || true

echo "==> Starting the app"
# detach fully so the SSH session that ran this script can close
./start.sh </dev/null >/dev/null 2>&1

echo "==> Waiting for $HEALTH_URL"
for _ in $(seq 1 45); do
  if curl -fsS "$HEALTH_URL" 2>/dev/null | grep -q '"status":"ok"'; then
    echo "==> Deployed $(git rev-parse --short HEAD)"
    exit 0
  fi
  sleep 2
done

echo "!! The app did not become healthy; last log lines:"
tail -n 60 uvicorn.log || true
exit 1
