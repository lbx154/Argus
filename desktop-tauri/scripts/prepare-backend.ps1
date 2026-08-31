[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "build\argus-backend"
$destination = Join-Path $root "resources\argus-backend"

if (-not (Test-Path -LiteralPath (Join-Path $source "argus-backend.exe") -PathType Leaf)) {
    throw "Frozen backend is missing at $source. Run npm --prefix desktop-tauri run build:backend first."
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
Get-ChildItem -LiteralPath $destination -Force | Where-Object {
    $_.Name -ne ".gitkeep"
} | Remove-Item -Recurse -Force
Copy-Item -Path (Join-Path $source "*") -Destination $destination -Recurse -Force
Write-Host "Prepared Tauri backend resource: $destination"
