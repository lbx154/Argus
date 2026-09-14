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
        if ($explicitCommand) { return $explicitCommand.Source }
        if (Test-Path -LiteralPath $PythonExecutable -PathType Leaf) {
            return (Resolve-Path -LiteralPath $PythonExecutable).Path
        }
        throw "Python executable not found: $PythonExecutable"
    }
    if ($env:ARGUS_BUILD_PYTHON) {
        if (Test-Path -LiteralPath $env:ARGUS_BUILD_PYTHON -PathType Leaf) {
            return $env:ARGUS_BUILD_PYTHON
        }
        throw "ARGUS_BUILD_PYTHON is not an executable file."
    }
    $repoPython = Join-Path $repo ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $repoPython -PathType Leaf) { return $repoPython }
    if ($env:VIRTUAL_ENV) {
        $activePython = Join-Path $env:VIRTUAL_ENV "Scripts\python.exe"
        if (Test-Path -LiteralPath $activePython -PathType Leaf) { return $activePython }
    }
    $fallbackCommand = Get-Command -Name "python" -ErrorAction SilentlyContinue
    if ($fallbackCommand) { return $fallbackCommand.Source }
    throw "No Python interpreter was found. Create a project environment or pass -PythonExecutable."
}

$backendPython = Resolve-BackendPython
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
Write-Host "using backend Python: $backendPython"

if (-not $SkipInstall) {
    & $backendPython -c "import sys; raise SystemExit(sys.prefix == sys.base_prefix)"
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation requires a project virtual environment. Use -SkipInstall if dependencies are already installed."
    }
    & $backendPython -m pip install "pyinstaller>=6.11,<7"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# One Windows build path owns identity + Web/TUI, the first-party native
# adapter, PyInstaller, frozen probes and verified backend resource staging.
# It refuses old output directories instead of deleting or overwriting them.
& $backendPython (Join-Path $PSScriptRoot "build-windows-backend.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
