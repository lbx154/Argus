[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$ReleaseTag,
    [Parameter(Mandatory)]
    [string]$OssPrefix,
    [Parameter(Mandatory)]
    [string]$PublicBaseUrl,
    [string]$GitHubRepository = "lbx154/Argus",
    [string]$OssUtil = "ossutil",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

# This is intentionally opt-in. It is a release-operator action that writes to
# object storage; ordinary local builds and tests must never invoke it.
if (-not $Apply) {
    throw "Refusing to upload without -Apply. Dry-run is intentionally fail-closed."
}
if ($PublicBaseUrl -notmatch '^https://') {
    throw "PublicBaseUrl must use HTTPS so the updater never downgrades transport security."
}

$root = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $root (".update-stage-" + $ReleaseTag)
Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $stage -Force | Out-Null
try {
    & gh release download $ReleaseTag --repo $GitHubRepository --dir $stage --pattern "latest.json" --pattern "Argus*.exe" --pattern "Argus*.exe.sig"
    if ($LASTEXITCODE -ne 0) { throw "gh release download failed with exit code $LASTEXITCODE" }

    $manifestPath = Join-Path $stage "latest.json"
    if (-not (Test-Path -LiteralPath $manifestPath)) { throw "latest.json was not found in release $ReleaseTag" }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $base = "https://github.com/$GitHubRepository/releases/download/"
    foreach ($platform in $manifest.platforms.PSObject.Properties) {
        $url = [string]$platform.Value.url
        if (-not $url.StartsWith($base, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Manifest contains a non-GitHub release URL: $url"
        }
        # The updater signature covers the bytes at url, not the URL text. The
        # copied .exe stays byte-identical and is verified before install.
        $platform.Value.url = $url.Replace($base, ($PublicBaseUrl.TrimEnd('/') + "/releases/download/"))
    }
    $manifestJson = $manifest | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText(
        $manifestPath,
        ($manifestJson + [Environment]::NewLine),
        (New-Object System.Text.UTF8Encoding($false))
    )

    $remote = $OssPrefix.TrimEnd('/')
    $assets = Get-ChildItem -LiteralPath $stage -File | Where-Object {
        $_.Name -match '^Argus.+\.exe(\.sig)?$'
    }
    if (-not $assets) { throw "No signed Argus installer assets were downloaded" }
    foreach ($asset in $assets) {
        & $OssUtil cp -f $asset.FullName "$remote/releases/download/$ReleaseTag/$($asset.Name)"
        if ($LASTEXITCODE -ne 0) { throw "ossutil failed to upload $($asset.Name)" }
    }
    & $OssUtil cp -f $manifestPath "$remote/latest.json" --headers "Cache-Control:no-cache, no-store, must-revalidate"
    if ($LASTEXITCODE -ne 0) { throw "ossutil failed to upload latest.json" }

    Write-Host "Uploaded signed update mirror for $ReleaseTag. Verify SHA-256 of each .exe against GitHub before adding the OSS endpoint to a release build."
}
finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}
