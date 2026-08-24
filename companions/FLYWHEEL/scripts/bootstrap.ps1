[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $VenvPython)) {
    python -m venv (Join-Path $ProjectRoot '.venv')
}

& $VenvPython -m pip install --disable-pip-version-check -e "$ProjectRoot\backend[test]"
npm --prefix (Join-Path $ProjectRoot 'frontend') install

Write-Host 'ARGUS / FLYWHEEL dependencies are ready.'
Write-Host 'Argus control plane: http://127.0.0.1:8799 (override with FLYWHEEL_ARGUS_BASE_URL).'
Write-Host 'Provider/model/API-key settings stay in Argus; Flywheel delegates every launched session to that configuration.'
Write-Host "Backend: $VenvPython -m uvicorn foundry.app:app --app-dir backend\src --host 127.0.0.1 --port 8743"
Write-Host 'Frontend: npm --prefix frontend run dev -- --host 127.0.0.1 --port 5175'
