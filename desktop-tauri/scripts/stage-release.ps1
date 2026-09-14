[CmdletBinding()]
param(
    [string]$BundleDirectory,
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
# Windows-only compatibility entry point. The staging implementation verifies
# real package signatures and refuses existing outputs; it never clears a prior
# release. CARGO_TARGET_DIR/CARGO_BUILD_TARGET are respected when no bundle path
# is supplied. No private signing material is read by this verification step.
$node = (Get-Command node -ErrorAction Stop).Source
$arguments = @((Join-Path $PSScriptRoot "stage-windows-release.mjs"))
if ($BundleDirectory) { $arguments += @("--bundle-dir", $BundleDirectory) }
if ($OutputDirectory) { $arguments += @("--output-dir", $OutputDirectory) }
& $node @arguments
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
