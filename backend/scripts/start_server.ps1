$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "backend/.venv is missing. Run: uv sync --project backend --group dev"
}

Set-Location -LiteralPath $Root
& $Python -m uvicorn app.main:app --host 127.0.0.1 --port 8010

