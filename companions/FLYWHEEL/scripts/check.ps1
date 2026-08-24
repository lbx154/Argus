[CmdletBinding()]
param(
    [string]$FixtureGpuModel = 'CI fixture GPU (non-production)',
    [int]$FixtureGpuCount = 1,
    [int]$FixtureGpuHours = 24,
    [string]$FixtureApiBudget = 'CI fixture budget (non-production)'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $VenvPython)) {
    throw 'Missing .venv. Run scripts\bootstrap.ps1 first.'
}

Push-Location $ProjectRoot
try {
    $CatalogPath = Join-Path $ProjectRoot 'runtime\prompt-catalog\CATALOG.json'
    if (-not (Test-Path -LiteralPath $CatalogPath)) {
        # This ignored catalog is a deterministic verification fixture. It is never a
        # detected TeamProfile resource contract and is not eligible for launch.
        & $VenvPython scripts\export_prompts.py `
            --gpu-count $FixtureGpuCount `
            --gpu-model $FixtureGpuModel `
            --gpu-hours $FixtureGpuHours `
            --wall-clock-deadline '2026-09-01T18:00:00+08:00' `
            --max-parallel-jobs 1 `
            --api-budget $FixtureApiBudget `
            --output runtime\prompt-catalog
        if ($LASTEXITCODE -ne 0) { throw 'Prompt catalog generation failed.' }
    }

    & $VenvPython scripts\audit_catalog.py
    if ($LASTEXITCODE -ne 0) { throw 'Catalog audit failed.' }

    & $VenvPython -m pytest backend
    if ($LASTEXITCODE -ne 0) { throw 'Backend tests failed.' }

    npm --prefix frontend run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }

    & $VenvPython scripts\smoke_api.py
    if ($LASTEXITCODE -ne 0) { throw 'API smoke test failed.' }
}
finally {
    Pop-Location
}

Write-Host 'All ARGUS / FLYWHEEL checks passed.'
