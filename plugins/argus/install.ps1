param(
    [ValidateSet("codex", "claude", "all")]
    [string]$Target = "all"
)

$ErrorActionPreference = "Stop"
$Repo = "lbx154/Argus"
$Source = if ($env:ARGUS_INSTALL_SOURCE) {
    $env:ARGUS_INSTALL_SOURCE
} else {
    "argus @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
}

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw "Python 3.11+ from python.org is required."
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "Node.js 22.12+ is required."
}
node -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 22 || (major === 22 && minor >= 12) ? 0 : 1)"
if ($LASTEXITCODE -ne 0) {
    throw "Node.js 22.12+ is required."
}

py -m pip install --upgrade --force-reinstall $Source
if ($LASTEXITCODE -ne 0) {
    throw "Argus package installation failed."
}
py -c "from argus.plugin.mcp_server import mcp; assert mcp.name == 'argus'"
if ($LASTEXITCODE -ne 0) {
    throw "Argus plugin server verification failed."
}

$Installed = 0
if ($Target -eq "codex" -or ($Target -eq "all" -and (Get-Command codex -ErrorAction SilentlyContinue))) {
    if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
        throw "Codex is not installed."
    }
    codex plugin marketplace add $Repo --ref main
    if ($LASTEXITCODE -ne 0) { throw "Could not add the Codex plugin marketplace." }
    codex plugin add argus@argus
    if ($LASTEXITCODE -ne 0) { throw "Could not install the Codex Argus plugin." }
    $Installed += 1
}

if ($Target -eq "claude" -or ($Target -eq "all" -and (Get-Command claude -ErrorAction SilentlyContinue))) {
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
        throw "Claude Code is not installed."
    }
    claude plugin marketplace add $Repo
    if ($LASTEXITCODE -ne 0) { throw "Could not add the Claude plugin marketplace." }
    claude plugin install argus@argus
    if ($LASTEXITCODE -ne 0) { throw "Could not install the Claude Argus plugin." }
    $Installed += 1
}

if ($Installed -eq 0) {
    throw "Install Codex or Claude Code, then rerun this command."
}

Write-Host "Argus plugin installed. Start a new Codex session or run /reload-plugins in Claude Code."
