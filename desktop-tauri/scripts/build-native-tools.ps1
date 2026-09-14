[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$desktop = Split-Path -Parent $PSScriptRoot
$repo = Split-Path -Parent $desktop
$source = Join-Path $PSScriptRoot "platon-headless.rs"
$output = Join-Path $repo "argus_skill\_native"
$tests = Join-Path $desktop "build\native-tests"
New-Item -ItemType Directory -Path $output, $tests -Force | Out-Null
$compiler = (Get-Command rustc -ErrorAction Stop).Source
$flags = @("--edition=2021", "--target", "x86_64-pc-windows-msvc", "-C", "target-feature=+crt-static", "-C", "link-arg=/Brepro")
$id = [Guid]::NewGuid().ToString("N")
$testBinary = Join-Path $tests "platon-headless-tests-$id.exe"
& $compiler @flags --test $source -o $testBinary
if ($LASTEXITCODE -ne 0) { throw "PLATON adapter tests could not compile. Use a verified MSVC environment." }
# The release builder isolates TEMP below its work tree. On GitHub's Windows
# disks, 8.3 aliases may be disabled, so that nested path cannot satisfy the
# scientific adapter's strict <80-character contract. Use the runner-owned
# short temporary root for these native tests only; every test still allocates
# its own exclusive directory, and the build environment is restored afterward.
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
try {
    if ($env:GITHUB_ACTIONS -eq "true" -and $env:RUNNER_TEMP) {
        if (-not (Test-Path -LiteralPath $env:RUNNER_TEMP -PathType Container)) {
            throw "The Windows runner temporary directory is unavailable."
        }
        $env:TEMP = $env:RUNNER_TEMP
        $env:TMP = $env:RUNNER_TEMP
    }
    & $testBinary
    if ($LASTEXITCODE -ne 0) { throw "PLATON adapter argument tests failed." }
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
}
$candidate = Join-Path $tests "platon-headless-$id.exe"
& $compiler @flags -O -C panic=abort $source -o $candidate
if ($LASTEXITCODE -ne 0) { throw "PLATON adapter build failed." }
$target = Join-Path $output "platon-headless.exe"
$expected = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash
if (Test-Path -LiteralPath $target) {
    if (((Get-Item -LiteralPath $target).Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Native build output must not be a link."
    }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $expected) {
        throw "Native adapter output already exists and differs. Use a fresh workspace; previous output was preserved."
    }
} else {
    $input = [System.IO.File]::OpenRead($candidate)
    try {
        $destination = [System.IO.File]::Open($target, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try { $input.CopyTo($destination); $destination.Flush($true) } finally { $destination.Dispose() }
    } finally { $input.Dispose() }
    if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $expected) {
        throw "Native adapter copy verification failed."
    }
}
Write-Host "native Windows adapter ready: $target"
