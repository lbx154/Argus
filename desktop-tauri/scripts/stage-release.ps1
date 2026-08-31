[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$package = Get-Content -LiteralPath (Join-Path $root "package.json") -Raw | ConvertFrom-Json
$version = [string]$package.version
$target = Join-Path $root "src-tauri\target\release"
$bundle = Join-Path $target "bundle\nsis"
$release = Join-Path $root "release"

if (-not (Test-Path -LiteralPath $bundle)) {
    throw "Tauri NSIS bundle was not found at $bundle"
}
Remove-Item -LiteralPath $release -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $release -Force | Out-Null

# The bundle directory is incremental and can contain previous local versions.
# Stage exactly this package version; never rename an arbitrary older installer
# into current-version release metadata merely because directory enumeration
# happened to return it first.
$escapedVersion = [regex]::Escape($version)
$installers = @(
    Get-ChildItem -LiteralPath $bundle -Filter "*.exe" -File | Where-Object {
        $_.Name -match "^Argus_${escapedVersion}_.+-setup\.exe$"
    }
)
if ($installers.Count -ne 1) {
    throw "Expected exactly one NSIS installer for version $version in $bundle; found $($installers.Count)."
}
$installer = $installers[0]
$installerTargetName = "Argus-$version-setup.exe"
$installerTarget = Join-Path $release $installerTargetName
Copy-Item -LiteralPath $installer.FullName -Destination $installerTarget -Force

# Tauri creates the detached package signature. latest.json is release metadata
# rather than a signed blob: its security anchor is the embedded signature for
# the package bytes, so we generate it only after a real .sig exists.
$signature = Get-ChildItem -LiteralPath $bundle -Filter "$($installer.Name).sig" -File -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $signature) {
    throw "No updater signature was produced. Set TAURI_SIGNING_PRIVATE_KEY; unsigned update metadata must never be staged."
}
$signatureTarget = "$installerTarget.sig"
Copy-Item -LiteralPath $signature.FullName -Destination $signatureTarget -Force
$signatureText = (Get-Content -LiteralPath $signature.FullName -Raw).Trim()
if ([string]::IsNullOrWhiteSpace($signatureText)) { throw "Updater signature is empty" }

$notes = [string]$env:ARGUS_DESKTOP_UPDATE_NOTES
if ([string]::IsNullOrWhiteSpace($notes)) {
    $notes = "Argus $version desktop update."
}
$manifest = [ordered]@{
    version = $version
    notes = $notes
    pub_date = (Get-Date).ToUniversalTime().ToString("o")
    platforms = [ordered]@{
        "windows-x86_64" = [ordered]@{
            url = "https://github.com/lbx154/Argus/releases/download/v$version/$installerTargetName"
            signature = $signatureText
        }
    }
}
$manifestPath = Join-Path $release "latest.json"
$manifestJson = $manifest | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText(
    $manifestPath,
    ($manifestJson + [Environment]::NewLine),
    (New-Object System.Text.UTF8Encoding($false))
)

Write-Host "Staged Tauri release artifacts in $release"
