# Clause — Legal Intelligence Copilot

[![CI](https://github.com/NIGHTFURY609/neu/actions/workflows/ci.yml/badge.svg)](https://github.com/NIGHTFURY609/neu/actions/workflows/ci.yml)

One application, with a public landing page, demo login, and connected legal-review workspace.

## Run the complete local demo

From Git Bash:

```bash
./dev.sh
```

From PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\dev.ps1
```

The launcher starts FastAPI on `http://localhost:8000` and the React app on
`http://localhost:5173`. It bootstraps missing frontend and backend dependencies on first run.

Local development defaults to `DEMO_MODE=true`, so the review queue, compliance dashboard,
source citations, and redlines are served from checked-in fixtures without needing Postgres.
The demo flow is:

```text
Landing page → Log in → Dashboard / Review queue → FastAPI fixture API
```

To run against a migrated Postgres/Supabase database instead, configure `DATABASE_URL` in
`backend/.env` and start with `DEMO_MODE=false ./dev.sh`.

If the default ports are occupied, choose another pair while keeping the proxy connected:

```bash
BACKEND_PORT=8001 FRONTEND_PORT=5174 ./dev.sh
```

## Verification

```bash
backend/.venv/Scripts/python.exe -m pytest --basetemp .pytest-tmp
npm --prefix frontend run test
npm --prefix frontend run build
```
