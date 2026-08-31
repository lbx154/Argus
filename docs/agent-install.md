# Argus installation instructions for coding agents

Complete the installation; do not merely print commands.

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
Node.js 22.12+, and one authenticated Agent CLI.

### Install — no virtual environment

```powershell
py -m pip install --upgrade pip
py -m pip install --upgrade --force-reinstall "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
$Scripts = py -c "import sysconfig; print(sysconfig.get_path('scripts'))"
$Argus = Join-Path $Scripts "argus.exe"
if (-not (Test-Path $Argus)) { throw "Argus entry point not found at $Argus" }
$env:Path = "$Scripts;$env:Path"
& $Argus --version
```

Do not ask the user to create or activate a venv on Windows. A packaged Desktop
installer may be used instead when a release provides one.

Always retain `--force-reinstall` while installing the moving preview: its
package version may stay unchanged when the archive contents change.

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

Windows supports Manager chat, pairing, Web/TUI, and terminal-scoped daemon
control. Detached subagents remain POSIX/WSL2-only and must fail explicitly on
native Windows.

## macOS

### Inspect

```bash
sw_vers
uname -m
uv --version
node --version
for cli in copilot codex claude agent cursor-agent pi opencode grok qodercli dsh; do command -v "$cli" || true; done
```

Require Node.js 22.12+, one authenticated Agent CLI, and uv. Install uv only with
the user's approval and its official installer.

### Install — uv-managed command, no manual venv

```bash
uv tool install --force --python 3.12 \
  "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
ARGUS_BIN="$(uv tool dir --bin)/argus"
test -x "$ARGUS_BIN"
"$ARGUS_BIN" --version
```

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
git clone https://github.com/lbx154/Argus.git "$HOME/Argus"
cd "$HOME/Argus"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
ARGUS_BIN="$HOME/Argus/.venv/bin/argus"
"$ARGUS_BIN" --version
```

Private-preview collaborators may use the authorized private repository instead.
If the checkout already exists, inspect `git status`; update only a clean branch
with `git pull --ff-only`, then refresh the editable install.

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

On Windows use a PowerShell environment variable and backtick continuation.
On macOS/Linux replace `argus` with the exact executable established above.
Never paste the key into chat or commit it.

## Upgrade and deterministic verification

Use the same install command again on Windows/macOS, including
`--force-reinstall`/`--force`, then run the exact executable with
`doctor --advisor none --verify`. On Linux run
`"$HOME/Argus/.venv/bin/argus" update`, followed by the same deterministic
verification. Do not invoke a second Agent repair turn merely to prove an
unchanged installation.

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
