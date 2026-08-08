<#!
Run the complete Clause demo from Windows PowerShell.

  .\dev.ps1
  .\dev.ps1 -BackendPort 8001 -FrontendPort 5174
  .\dev.ps1 -Live  # requires backend/.env with DATABASE_URL
#>

[CmdletBinding()]
param(
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 5173,
  [switch]$Live
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Backend = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$Python = Join-Path $Backend '.venv\Scripts\python.exe'

if (-not (Test-Path $Python)) {
  Write-Host 'Creating backend virtualenv and installing development dependencies...'
  & py -m venv (Join-Path $Backend '.venv')
  & $Python -m pip install -e ($Backend + '[dev]')
}

if (-not (Test-Path (Join-Path $Frontend 'node_modules'))) {
  & npm.cmd --prefix $Frontend install
}

# Defaults false, same reasoning as dev.sh: app/api.py refuses to start with
# DEMO_MODE=true against a non-local DATABASE_URL, so demo mode has to be opted into
# rather than assumed. -Live no longer changes this — it's already the default — kept
# as an accepted no-op so existing `-Live` invocations don't break.
$env:DEMO_MODE = 'false'
$env:VITE_API_TARGET = "http://localhost:$BackendPort"
$env:FRONTEND_PORT = "$FrontendPort"

Write-Host "backend  -> http://localhost:$BackendPort  (docs at /docs)"
Write-Host "frontend -> http://localhost:$FrontendPort"

$backendProcess = Start-Process -FilePath $Python -WorkingDirectory $Backend -NoNewWindow -PassThru -ArgumentList @(
  '-m', 'uvicorn', 'app.api:app', '--reload', '--port', "$BackendPort"
)
$frontendProcess = Start-Process -FilePath 'npm.cmd' -WorkingDirectory $Frontend -NoNewWindow -PassThru -ArgumentList @(
  'run', 'dev'
)

try {
  while (-not $backendProcess.HasExited -and -not $frontendProcess.HasExited) {
    Start-Sleep -Milliseconds 400
    $backendProcess.Refresh()
    $frontendProcess.Refresh()
  }
} finally {
  foreach ($process in @($backendProcess, $frontendProcess)) {
    if (-not $process.HasExited) {
      Stop-Process -Id $process.Id -Force
    }
  }
}
