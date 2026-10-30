# Argus installation instructions for coding agents

Complete the installation; do not merely print commands.

## Installation channel

The commands below use the official source repository, `microsoft/ArgusAgent`.
They install its `main` branch, not a published binary release and not a PyPI
package. Use the development preview, `lbx154/Argus`, only when the user asks for
it; replace the repository in the selected platform's commands and use this
guide from that same repository.

For an upgrade, inspect the existing checkout remote or the installed package's
`direct_url.json` before choosing the source. Keep the existing channel unless
the user requests a switch. Never silently redirect an existing clone.

Windows packaged previews are separate from source installation. See
[Windows Desktop](windows-desktop.md) for the official and preview release
pages; an EXE may not yet contain fixes present on `main`.

Source installation includes a Web/TUI build. A pip ZIP installation such as
one originally downloaded from
`https://github.com/microsoft/ArgusAgent/archive/refs/heads/main.zip` does not run
npm and can contain old tracked assets. Use the platform source/build commands
below, or a wheel built after those commands. Preserve the existing repository
channel and choose a new directory if the suggested path already contains work.
Never report `install/assets_stale` as ready to use: rebuild before starting the
cockpit. Do not commit generated dist, bundle or release manifest files.

## Safety contract

- Argus has no separate Argus account. Use one supported Agent CLI and its
  official login flow.
- Inspect before changing the machine.
- Ask before `sudo`, system package installation, shell startup edits, or global
  configuration changes.
- Never request passwords, tokens, or API keys in chat. Never place credentials
  in the Argus checkout or shell history.
- Prefer the Agent CLI already running this conversation.
- Do not replace a dirty checkout or silently switch providers after a failure.
- Use only the section for the detected operating system.
- Running `argus doctor` is read-only. Only an explicit
  `argus doctor --advisor <auto|backend>` authorizes the selected installed
  Agent to inspect and repair Argus files, configuration, runtime state, and
  required dependencies. Login or administrator blockers must be reported
  rather than guessed.

Supported backend values:

| Agent CLI | Backend |
|---|---|
| GitHub Copilot CLI | `copilot` |
| OpenAI Codex CLI | `codex` |
| Claude Code | `claude` |
| Cursor CLI | `cursor` |
| Pi | `pi` |
| OpenCode | `opencode` |
| xAI Grok Build | `grok` |
| Qoder CLI | `qoder` |
| DeepSeek Harness | `dsh` |

Setup adopts a model from the selected CLI's own catalog when available and
otherwise keeps its native default. Never assign an OpenAI model id to Claude
Code, Pi, OpenCode, Grok, Qoder, or dsh merely because it is Argus's historical
default.

## Dedicated Copilot account

To charge Argus's Copilot work to a different account from the terminal or Agent
that launches it, select a separate Copilot directory. Install Copilot CLI first,
then run this in an interactive terminal (PowerShell also accepts this path):

```sh
argus --setup --backend copilot --copilot-home "$HOME/.copilot-argus" --copilot-login
```

`--copilot-login` invokes the official `copilot login` with isolated child
environment variables. Choose the intended account in the browser. Copilot
owns credentials and keychain/plaintext-storage consent; Argus never asks for
or saves your token. Do not copy another account's configuration into this
directory. On a headless host, answer any official storage-consent prompt in
the terminal.

For a directory that is already authenticated, omit login:

```sh
argus --setup --backend copilot --copilot-home "$HOME/.copilot-argus" --non-interactive
```

Setup checks authentication and completes a real, potentially billable model
turn before atomically saving the backend profile and the absolute directory
as `ARGUS_SKILL_COPILOT_HOME` in Argus's `config.json`. On failure the previous
binding remains; a successful official login is not undone if a later check
fails. `--copilot-login` cannot be combined with `--non-interactive`.

Restart existing Argus TUI, Web/API and daemon processes after changing the
binding, so they pick up the new account. Subsequent ordinary `argus` launches
using the same `ARGUS_SKILL_HOME` use this account for every Copilot worker.
Other Agent backends are unaffected.

The environment setting `ARGUS_SKILL_COPILOT_HOME` overrides the persisted
directory. A dedicated binding overrides inherited `COPILOT_HOME`, removes
`COPILOT_GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_TOKEN`, `COPILOT_OFFLINE` and
`COPILOT_PROVIDER_*` from Copilot children, and redirects their `GH_CONFIG_DIR`
to `<dedicated-home>/gh`. The invoking shell and its login are unchanged. Argus
does not mirror personal authentication or run session cleanup in this directory.
Worktree-isolated maintenance workers reference the selected account's credential
files read-only; their writable Copilot state and usage database stay in that
worktree's private runtime, not the shared account directory.
If it is unavailable or unauthenticated, do not fall back to another account:
fix the directory/login and rerun setup.

Remove any environment override before restoring the shared-account behavior:

```sh
argus --setup --backend copilot --copilot-home= --non-interactive
```

An explicitly empty `ARGUS_SKILL_COPILOT_HOME` also disables the saved binding for
that process. Clearing the binding does not log out or delete the dedicated
directory. Without this opt-in, existing account-selection behavior is unchanged.
Account separation is not an OS sandbox. An outer Copilot conversation still has
its own usage; GitHub's usage page is authoritative for charges.

## Windows 10/11

### Inspect

Use PowerShell:

```powershell
[Environment]::OSVersion.VersionString
py --version
node --version
Get-Command copilot,codex,claude,agent,cursor-agent,pi,opencode,grok,qodercli,dsh -ErrorAction SilentlyContinue
```

Require Python 3.11+ from python.org with **Add Python to PATH** selected,
Node.js 22.12+/npm, Git, and one authenticated Agent CLI.

### Install — no virtual environment

```powershell
py -m pip install --upgrade pip build
git clone https://github.com/microsoft/ArgusAgent.git "$env:USERPROFILE\Argus"
Set-Location "$env:USERPROFILE\Argus"
npm ci
npm --prefix frontend/web ci
npm --prefix frontend/tui ci
py -m argus.release_tools.build_release
py -m pip install --upgrade --force-reinstall .
$Scripts = py -c "import sysconfig; print(sysconfig.get_path('scripts'))"
$Argus = Join-Path $Scripts "argus.exe"
if (-not (Test-Path $Argus)) { throw "Argus entry point not found at $Argus" }
$env:Path = "$Scripts;$env:Path"
& $Argus --version
```

Do not ask the user to create or activate a venv on Windows. A packaged Desktop
installer may be used instead when a release provides one.

The Windows install builds assets before pip packages the checkout. Retain
`--force-reinstall` when replacing the same package version with a new build.

### Configure and verify

```powershell
& $Argus --setup --non-interactive --backend <copilot|codex|claude|cursor|pi|opencode|grok|qoder|dsh>
& $Argus doctor --deep --advisor auto
& $Argus --status
```

`argus --setup` must finish its real Agent-turn smoke test. A package install or
version command alone is not success. Using `$Argus` proves the newly installed
entry point was tested instead of another copy earlier on PATH. If a later
window cannot find plain `argus`, report `$Scripts` and ask before changing the
user PATH; do not create a venv as a workaround.

Windows supports Manager chat, pairing, Web/TUI and terminal-scoped daemon
control. Each provider turn owns a private Job Object, so a long command a role
starts stays in that turn and is cleaned up with it (see
[process ownership](windows-process-ownership.md)). WSL2 is optional, not a
prerequisite for this path.

## macOS

### Inspect

```bash
sw_vers
uname -m
uv --version
node --version
for cli in copilot codex claude agent cursor-agent pi opencode grok qodercli dsh; do command -v "$cli" || true; done
```

Require Git, Node.js 22.12+/npm, one authenticated Agent CLI, and uv. Install uv only with
the user's approval and its official installer.

### Install — uv-managed command, no manual venv

```bash
git clone https://github.com/microsoft/ArgusAgent.git "$HOME/Argus"
cd "$HOME/Argus"
npm ci
npm --prefix frontend/web ci
npm --prefix frontend/tui ci
uv run --no-project --python 3.12 --with build python -m argus.release_tools.build_release
uv tool install --force --python 3.12 --reinstall .
ARGUS_BIN="$(uv tool dir --bin)/argus"
test -x "$ARGUS_BIN"
"$ARGUS_BIN" --version
```

`--reinstall` refreshes the local package build even when its version is unchanged.

### Configure and verify

```bash
"$ARGUS_BIN" --setup --non-interactive \
  --backend <copilot|codex|claude|cursor|pi|opencode|grok|qoder|dsh>
"$ARGUS_BIN" doctor --deep --advisor auto
"$ARGUS_BIN" --status
```

Setup is complete only after the real Agent-turn smoke succeeds. Keep using
`$ARGUS_BIN` in the current shell. With approval, run `uv tool update-shell` to
make plain `argus` available in new terminals.

With the explicit `--advisor auto` shown above, Doctor runs the installed
Agent with tools enabled, applies Argus-scoped repairs, and then reruns
deterministic verification. `argus doctor` without an advisor remains
read-only; use `--advisor none --verify` for an explicit non-Agent verification
run. Allow several minutes for an active repair because it performs a real
Agent turn and may repair dependencies.

## Linux

### Inspect

```bash
uname -a
python3 --version
node --version
git --version
for cli in copilot codex claude agent cursor-agent pi opencode grok qodercli dsh; do command -v "$cli" || true; done
```

Require Python 3.11+, Node.js 22.12+, Git, the distribution's `python3-venv`
package, and one authenticated Agent CLI.

### Install — persistent source venv

Choose a persistent directory. Default to `$HOME/Argus` only when it does not
already contain unrelated data:

```bash
git clone https://github.com/microsoft/ArgusAgent.git "$HOME/Argus"
cd "$HOME/Argus"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e . build
npm ci
npm --prefix frontend/web ci
npm --prefix frontend/tui ci
.venv/bin/python -m argus.release_tools.build_release
ARGUS_BIN="$HOME/Argus/.venv/bin/argus"
"$ARGUS_BIN" --version
```

Private-preview collaborators may use the authorized private repository instead.
If the checkout already exists, inspect `git status`; update only a clean branch
with `git pull --ff-only`, then repeat all dependency and asset-build commands
before launching. A previously built checkout has generated local changes; use
a fresh checkout for the upgrade rather than discarding local work.

### Configure and verify

```bash
cd "$HOME/Argus"
"$ARGUS_BIN" --setup --non-interactive \
  --backend <copilot|codex|claude|cursor|pi|opencode|grok|qoder|dsh>
"$ARGUS_BIN" doctor --deep --advisor auto
"$ARGUS_BIN" --status
```

Linux keeps the explicit venv because server Python/CUDA dependencies and
long-running process ownership must remain reproducible. Never substitute a
global `argus`; it may be stale. If venv creation reports missing `ensurepip`,
install the distribution's `python3-venv` package and retry.

Wheel packaging must follow these same dependency and asset-build steps.
From the rebuilt checkout, run `python -m argus.release_tools.check_artifacts`
and `python -m build --wheel` with its build interpreter (`py` on Windows,
`.venv/bin/python` on Linux, or `uv run --no-project --python 3.12 --with build python`
on macOS). Install that wheel file; do not substitute a raw source ZIP. See
[wheel commands](getting-started.md#upgrade-and-wheel-packaging).

## Confirm the backend model selector

Setup validates the model it will send before reporting success. Also run
`<exact-argus-executable> --config-help` and inspect each role's effective value
and source.
Backend catalog commands include `pi --list-models`, `opencode auth list`, and
`qodercli --list-models`. If the selected id is not in that account's catalog,
set `ARGUS_SKILL_MODEL` or a role-specific model knob before rerunning setup.
Do not silently switch providers after a failed readiness check.

## OpenAI-compatible endpoint

Setup can configure Pi directly:

```bash
ARGUS_SETUP_API_KEY=... argus --setup --non-interactive \
  --api-url https://api.example.com/v1 \
  --api-model model-id
```

If `PI_CODING_AGENT_DIR` is set, setup writes `models.json` in that directory,
matching the Pi CLI. Otherwise it uses `~/.pi/agent/models.json`. Keep the same
environment when launching Argus so Pi reads the configuration that setup wrote.

On Windows use a PowerShell environment variable and backtick continuation.
On macOS/Linux replace `argus` with the exact executable established above.
Never paste the key into chat or commit it.

## Upgrade and deterministic verification

A source upgrade includes dependency installation and Web/TUI asset rebuilding.
Use a fresh persistent checkout and repeat this guide's platform installation
commands, selecting the existing repository channel. The [README update section](../README.md#update)
gives complete Windows, macOS and Linux commands with a new directory. Preserve
the old checkout and state; a previous build leaves generated local changes.
For a clean, attached checkout updated with `git pull --ff-only`, repeat every
npm installation and `build_release` step before starting the new server.

`argus update`, `argus --update` and `argus -update` only update/reinstall Python;
they do not build assets. Neither does a direct pip ZIP install. Do not treat
`& $Argus update`, `"$(uv tool dir --bin)/argus" update`, or
`"$HOME/Argus/.venv/bin/argus" update` as a completed source upgrade without the
asset-build steps. For older versions, use the same source-build installation
commands rather than bootstrapping with an unbuilt archive.

After switching, use the exact installed executable with `--version` and
`doctor --advisor none --verify`, then check the webpage served by the new
process. Do not invoke an Agent repair turn merely to verify the installation.
Packaged Desktop installers keep their separate signed update channel.

## Completion report

Report:

- operating system and installation method;
- exact executable used for Argus;
- selected Agent CLI/backend;
- effective model and configuration source for each role;
- whether setup's real Agent turn passed;
- whether `argus doctor --deep --advisor auto` passed;
- exact launch command;
- remaining manual login or PATH action.

If setup or Doctor fails, report the failing stage, executable, concise error,
and exact next command. Do not claim installation success.
