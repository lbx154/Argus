[CmdletBinding()]
param(
    [switch]$SkipInstall,
    [string]$PythonExecutable
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$repo = Split-Path -Parent $root

function Resolve-BackendPython {
    if ($PythonExecutable) {
        $explicitCommand = Get-Command -Name $PythonExecutable -ErrorAction SilentlyContinue
        if ($explicitCommand) {
            return $explicitCommand.Source
        }
        if (Test-Path -LiteralPath $PythonExecutable -PathType Leaf) {
            return (Resolve-Path -LiteralPath $PythonExecutable).Path
        }
        throw "Python executable not found: $PythonExecutable"
    }

    # Prefer the repository environment. On Windows a bare `python` commonly
    # resolves to an unrelated Anaconda or Store installation which can run
    # PyInstaller but cannot import this checkout's argus_skill package.
    $repoPython = Join-Path $repo ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $repoPython -PathType Leaf) {
        return $repoPython
    }

    if ($env:VIRTUAL_ENV) {
        $activePython = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
        if (Test-Path -LiteralPath $activePython -PathType Leaf) {
            return $activePython
        }
    }

    $fallbackCommand = Get-Command -Name "python" -ErrorAction SilentlyContinue
    if ($fallbackCommand) {
        return $fallbackCommand.Source
    }
    throw "No Python interpreter was found. Create $repoPython or pass -PythonExecutable."
}

$backendPython = Resolve-BackendPython
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Write-Host "using backend Python: $backendPython"

if (-not $SkipInstall) {
    & $backendPython -m pip install "pyinstaller>=6.11,<7"
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

& $backendPython -c "import argus_skill"
if ($LASTEXITCODE -ne 0) {
    Write-Error "The selected Python cannot import argus_skill. Install this checkout into that environment (python -m pip install -e '$repo') or pass -PythonExecutable."
    exit $LASTEXITCODE
}

Push-Location $repo
try {
    & $backendPython -m PyInstaller `
        --noconfirm `
        --clean `
        "$root\argus_backend.spec" `
        --distpath "$root\build" `
        --workpath "$root\build\.work"
}
finally {
    Pop-Location
}

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$backend = Join-Path $root "build\argus-backend\argus-backend.exe"
if (-not (Test-Path -LiteralPath $backend)) {
    Write-Error "PyInstaller did not produce $backend"
    exit 1
}

function Assert-BackendCommand {
    param(
        [string]$Label,
        [string[]]$CommandArgs
    )
    Write-Host "verifying $Label"
    & $backend @CommandArgs
    if ($LASTEXITCODE -ne 0) {
        $verifyExitCode = $LASTEXITCODE
        Write-Error "$Label failed with exit code $verifyExitCode"
        exit $verifyExitCode
    }
}

Assert-BackendCommand `
    -Label "frozen vertical/domain providers: $backend" `
    -CommandArgs @("--verify-frozen-runtime")
Assert-BackendCommand `
    -Label "frozen isolated internal module dispatch" `
    -CommandArgs @("-I", "-m", "argus_skill.tools.manager_live_view", "--help")
Assert-BackendCommand `
    -Label "frozen Python-compatible -c dispatch" `
    -CommandArgs @("-c", "import argus_skill; print(argus_skill.__version__)")
Assert-BackendCommand `
    -Label "frozen Windows IANA timezone data" `
    -CommandArgs @("-c", "from zoneinfo import ZoneInfo; print(ZoneInfo('Asia/Shanghai').key)")

$scriptProbe = Join-Path $env:TEMP "argus-frozen-script-probe-$PID.py"
try {
    Set-Content `
        -LiteralPath $scriptProbe `
        -Value "import argus_skill; print('script-ok', argus_skill.__version__)" `
        -Encoding UTF8
    Assert-BackendCommand `
        -Label "frozen Python-compatible script dispatch" `
        -CommandArgs @($scriptProbe)
}
finally {
    Remove-Item -LiteralPath $scriptProbe -Force -ErrorAction SilentlyContinue
}

Write-Host "backend ready: $backend"
