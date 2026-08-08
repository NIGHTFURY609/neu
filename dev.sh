#!/usr/bin/env bash
# Run the backend (uvicorn :8000) and the frontend (vite :5173) together.
#
#   ./dev.sh
#
# Vite proxies /api -> localhost:8000, so both halves must be up for the dashboard to
# show anything. Ctrl+C stops both.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

# Git Bash on Windows puts the venv in Scripts/, POSIX in bin/. Bootstrap it
# automatically so a fresh clone has one command to start the whole demo.
if [ -x "$BACKEND/.venv/Scripts/python.exe" ]; then
  PY="$BACKEND/.venv/Scripts/python.exe"
elif [ -x "$BACKEND/.venv/bin/python" ]; then
  PY="$BACKEND/.venv/bin/python"
else
  echo "Creating backend virtualenv and installing development dependencies..."
  PYTHON_BIN="${PYTHON_BIN:-python}"
  "$PYTHON_BIN" -m venv "$BACKEND/.venv"
  if [ -x "$BACKEND/.venv/Scripts/python.exe" ]; then
    PY="$BACKEND/.venv/Scripts/python.exe"
  else
    PY="$BACKEND/.venv/bin/python"
  fi
  "$PY" -m pip install -e "$BACKEND[dev]"
fi

# app/db.py builds the engine at import time, so a driver mismatch here kills the app
# before it serves a byte. pyproject declares psycopg v3; SQLAlchemy maps a bare
# postgresql:// URL to psycopg2, which is not installed.
if grep -qE '^\s*DATABASE_URL\s*=\s*postgresql://' "$BACKEND/.env" 2>/dev/null; then
  echo "warning: backend/.env DATABASE_URL uses 'postgresql://' — SQLAlchemy will look" >&2
  echo "         for psycopg2. Change it to 'postgresql+psycopg://' to use psycopg v3." >&2
fi

[ -d "$FRONTEND/node_modules" ] || (cd "$FRONTEND" && npm install)

# The checked-in fixtures power a fully interactive local demo. Set DEMO_MODE=true to
# use them instead of a database. Defaults false: app/api.py refuses to start with
# DEMO_MODE=true against anything that isn't a local/disposable DATABASE_URL, so forcing
# demo mode on by default would crash a machine that already has a real one configured.
export DEMO_MODE="${DEMO_MODE:-false}"
export VITE_API_TARGET="http://localhost:$BACKEND_PORT"
export FRONTEND_PORT

pids=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "backend  -> http://localhost:$BACKEND_PORT  (docs at /docs)"
(cd "$BACKEND" && "$PY" -m uvicorn app.api:app --reload --port "$BACKEND_PORT") &
pids+=($!)

echo "frontend -> http://localhost:$FRONTEND_PORT"
(cd "$FRONTEND" && npm run dev) &
pids+=($!)

# Exit as soon as either half dies, rather than leaving a half-up stack that looks like
# a backend outage in the UI.
wait -n
echo "one of the two processes exited — shutting the other down." >&2
