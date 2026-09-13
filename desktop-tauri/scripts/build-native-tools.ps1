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
$testBinary = Join-Path $tests "platon-headless-tests-$PID.exe"
& $compiler @flags --test $source -o $testBinary
if ($LASTEXITCODE -ne 0) { throw "PLATON adapter tests could not compile. Use a verified MSVC environment." }
& $testBinary
if ($LASTEXITCODE -ne 0) { throw "PLATON adapter argument tests failed." }
& $compiler @flags -O -C panic=abort $source -o (Join-Path $output "platon-headless.exe")
if ($LASTEXITCODE -ne 0) { throw "PLATON adapter build failed." }
Write-Host "native Windows adapter ready: $output\platon-headless.exe"
