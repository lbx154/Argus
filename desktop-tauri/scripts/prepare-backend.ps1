[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
# Keep the legacy Windows entry point, but use the same identity checks and
# non-overwriting, SHA-256 verified staging as npm build/prepare.
$node = (Get-Command node -ErrorAction Stop).Source
& $node (Join-Path $PSScriptRoot "desktop-build.mjs") prepare
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
