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

# Git Bash on Windows puts the venv in Scripts/, POSIX in bin/.
if [ -x "$BACKEND/.venv/Scripts/python.exe" ]; then
  PY="$BACKEND/.venv/Scripts/python.exe"
elif [ -x "$BACKEND/.venv/bin/python" ]; then
  PY="$BACKEND/.venv/bin/python"
else
  echo "No virtualenv at backend/.venv — create one and 'pip install -e .[dev]' first." >&2
  exit 1
fi

# app/db.py builds the engine at import time, so a driver mismatch here kills the app
# before it serves a byte. pyproject declares psycopg v3; SQLAlchemy maps a bare
# postgresql:// URL to psycopg2, which is not installed.
if grep -qE '^\s*DATABASE_URL\s*=\s*postgresql://' "$BACKEND/.env" 2>/dev/null; then
  echo "warning: backend/.env DATABASE_URL uses 'postgresql://' — SQLAlchemy will look" >&2
  echo "         for psycopg2. Change it to 'postgresql+psycopg://' to use psycopg v3." >&2
fi

[ -d "$FRONTEND/node_modules" ] || (cd "$FRONTEND" && npm install)

pids=()
cleanup() {
  trap - INT TERM EXIT
  for pid in "${pids[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "backend  -> http://localhost:8000  (docs at /docs)"
(cd "$BACKEND" && "$PY" -m uvicorn app.api:app --reload --port 8000) &
pids+=($!)

echo "frontend -> http://localhost:5173"
(cd "$FRONTEND" && npm run dev) &
pids+=($!)

# Exit as soon as either half dies, rather than leaving a half-up stack that looks like
# a backend outage in the UI.
wait -n
echo "one of the two processes exited — shutting the other down." >&2
