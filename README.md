<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/svg/argus-logo-horizontal-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/brand/svg/argus-logo-horizontal.svg">
  <img src="docs/assets/brand/svg/argus-logo-horizontal.svg" width="420" alt="Argus">
</picture>

### Persistent, reviewed autonomy for research and engineering

Long-running agent work that can plan, execute, verify, pause, and continue beyond a single model turn.

**Preview v0.1.2 · Preview channel for upcoming Argus updates.**

[![GitHub Stars](https://img.shields.io/github/stars/lbx154/Argus?style=flat-square)](https://github.com/lbx154/Argus/stargazers)
[![License](https://img.shields.io/github/license/lbx154/Argus?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![arXiv](https://img.shields.io/badge/arXiv-2608.05144-b31b1b?style=flat-square&logo=arxiv&logoColor=white)](https://arxiv.org/abs/2608.05144)

[Website](https://argusbot.cn) · [Video Demo](https://www.youtube.com/watch?v=i8Qy9HCboQE) · [Technical Report · arXiv:2608.05144](https://arxiv.org/pdf/2608.05144) · [WeChat Community](#wechat-community) · **English** / [简体中文](README.zh-CN.md)

`Manager` → `Planner` → `Engineer` ⇄ `Reviewer`

</div>

---

> [!IMPORTANT]
> **Repository status:** This is the Argus preview repository. The official
> release is maintained at
> **[microsoft/ArgusAgent](https://github.com/microsoft/ArgusAgent)**. Updates
> are synchronized between both repositories; Watch or Star either repository
> to follow the project.

## The Driver–Harness Model

A **model** is an engine: it burns compute and puts out tokens. A **harness** is the
drivetrain that couples those tokens to files, shells, compilers, GPUs, and tests. The
**Driver** is the seat — choosing what to do next, judging whether the last result was
any good, and knowing when to stop and ask. In every other agent system that seat holds a
human, which is why the work stops when they go to bed.

**Argus takes the Driver's seat**, splitting it across four roles deliberately not allowed
to do each other's jobs:

| | Owns | May **not** |
|---|---|---|
| **Manager** | Stage transitions, and where an admitted lesson is kept | Perform the work it is admitting |
| **Planner** | The next task, and the evidence it must produce | Move the campaign to the next stage |
| **Engineer** | Implementation, research, experiments, artifacts | Declare its own work complete |
| **Reviewer** | The verdict — correctness, evidence, limitations; may return `blocked` | Edit anything. It runs **read-only** |

Credentials, payment, irreversible actions, and publication always stop for a human.

It also improves without retraining: admitted Skills and source-linked Wiki findings are
scoped `project` → `vertical` → `global` by how far they were shown to hold, and new
domains ship as **verticals** against a core that does not change — 24 of them, with zero
references to the authority boundary across 53,871 lines of domain code.

Because the worker cannot grade its own work, nobody has to watch it: across 27 campaigns
and 1,548 hours it needed a human research decision about **once every 310 hours**, at
**95–99%** duty cycle. Everything else is in the
**[technical report](https://arxiv.org/pdf/2608.05144)**.

**Native backends:** `GitHub Copilot CLI` · `Pi` · `OpenAI Codex CLI` · `Claude Code` · `OpenCode` · `Grok Build` · `Qoder` · `DeepSeek Harness`

**Harbor evaluation:** Harbor Framework can invoke the complete bounded Argus
Manager/Planner/Engineer/Reviewer runtime as a custom agent. See
**[Harbor integration](docs/harbor.md)**.

**Coding-agent plugin:** use the packaged MCP bridge and host-specific Skills
without changing the core runtime. See **[Plugin quick start](docs/plugin.md)**.

## WeChat community

Scan the QR code to join the Argus community. Click the image to open it at full
size. If the printed expiry date has passed, open an Issue and ask the
maintainers for the latest code.

<p align="center">
  <a href="docs/assets/argus-wechat-group.jpg">
    <img src="docs/assets/argus-wechat-group.jpg" width="360" alt="Argus WeChat community QR code">
  </a>
</p>

## Quick Install

Choose the section for your operating system. Do not mix commands between
platforms. All platforms need Node.js **22.12+** from
[nodejs.org](https://nodejs.org/en/download) and one authenticated Agent CLI.
Reuse the CLI you already work in; Argus does not require a separate account.
Docker is not required for a normal Argus installation; it is only an optional
prerequisite for the separate Harbor evaluation integration.

> [!TIP]
> **Recommended: let the Code Agent you already use install and verify Argus.**
> Copy the prompt in the Agent-assisted section below. The manual commands remain
> available for users who prefer to install each step themselves.

| Agent CLI | Backend | Install | Authenticate |
|---|---|---|---|
| GitHub Copilot CLI | `copilot` | `npm install -g @github/copilot` | `copilot login` |
| OpenAI Codex CLI | `codex` | `npm install -g @openai/codex@latest` | `codex login` |
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` | Run `claude`, then `/login` |
| Pi | `pi` | `npm install -g --ignore-scripts @earendil-works/pi-coding-agent` | Run `pi`, then `/login` |
| OpenCode | `opencode` | [Official install](https://opencode.ai/docs/) | `opencode auth login` |
| Grok Build | `grok` | [Official install](https://x.ai/cli) | `grok login` |
| Qoder CLI | `qoder` | `npm install -g @qoder-ai/qodercli` | `qodercli login` |
| DeepSeek Harness | `dsh` | `npm install -g @deepseek-ai/dsh` | Configure `DEEPSEEK_API_KEY` or the dsh Models page |

The public preview is installed directly from the current GitHub archive until
the first PyPI release is published.

### Recommended: Agent-assisted installation

Send this prompt to an already installed Code Agent:

```text
Read https://github.com/lbx154/Argus/blob/main/docs/agent-install.md and install
Argus using the section for this operating system. Prefer the Agent CLI running
this conversation as the Argus backend. Do not create a venv on Windows or
macOS; keep the documented venv on Linux. Run setup through its real Agent-turn
smoke test, then run `argus doctor --deep --advisor auto`. Before account login,
sudo, or global configuration changes, explain why and wait for approval. Never
ask me to paste a password, token, or API key into the conversation.
```

The agent follows the **[installation execution contract](docs/agent-install.md)**.

### Windows 10/11 — direct pip, no virtual environment

Install Python 3.11+ from [python.org](https://www.python.org/downloads/windows/)
and select **Add Python to PATH** in the installer. Then open a new PowerShell:

```powershell
py --version
node --version
py -m pip install --upgrade pip
py -m pip install --upgrade --force-reinstall "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
$Scripts = py -c "import sysconfig; print(sysconfig.get_path('scripts'))"
$Argus = Join-Path $Scripts "argus.exe"
if (-not (Test-Path $Argus)) { throw "Argus entry point not found at $Argus" }
$env:Path = "$Scripts;$env:Path"
& $Argus --version
& $Argus --setup
& $Argus doctor --deep --advisor auto
& $Argus --status
& $Argus
```

Calling `$Argus` proves setup is not accidentally using another stale
installation. `$env:Path` also makes plain `argus` available in the current
PowerShell. The troubleshooting section covers persistent PATH repair.

`argus doctor` is an active repair command. By default it launches an installed
Agent CLI in the real Argus directories with tools enabled, lets the Agent
inspect and fix the machine, then reruns deterministic checks. Use
`argus doctor --advisor none --verify` for a no-model verification.
The active repair may take several minutes because it performs a real Agent
turn; it is not a quick version check.

Windows currently supports installation, Manager chat, pairing, Web/TUI,
terminal-scoped daemon control, and native durable subagents. On native Windows,
a detached worker owns direct or supervised long commands, persists registry and
log state, and uses bounded process-tree cleanup; WSL2 remains optional rather
than required for this path. The Windows Desktop installer is documented separately in
**[Windows Desktop](docs/windows-desktop.md)**.

### macOS — managed command install, no manual virtual environment

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed,
then:

```bash
uv --version
node --version
uv tool install --force --python 3.12 \
  "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
ARGUS_BIN="$(uv tool dir --bin)/argus"
test -x "$ARGUS_BIN"
"$ARGUS_BIN" --version
uv tool update-shell
"$ARGUS_BIN" --setup
"$ARGUS_BIN" doctor --deep --advisor auto
"$ARGUS_BIN" --status
"$ARGUS_BIN"
```

`ARGUS_BIN` works immediately even when uv's tool directory was not previously
on PATH. `uv tool update-shell` makes plain `argus` available in a new terminal.
`uv tool` already owns the isolated environment; do not create another venv.

### Linux — isolated source venv

Linux servers keep an explicit venv so Python, CUDA tooling, and long-running
process ownership remain reproducible. Install Python 3.11+, Git, Node.js
22.12+, and your distribution's `python3-venv` package first:

```bash
git clone https://github.com/lbx154/Argus.git "$HOME/Argus"
cd "$HOME/Argus"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
ARGUS_BIN="$HOME/Argus/.venv/bin/argus"
"$ARGUS_BIN" --version
"$ARGUS_BIN" --setup
"$ARGUS_BIN" doctor --deep --advisor auto
"$ARGUS_BIN" --status
"$ARGUS_BIN"
```

Private-preview collaborators use
`https://github.com/lbx154/argus-skill.git` in the Linux clone command. On
Windows/macOS, install a private wheel or authenticated private archive rather
than putting a GitHub token in shell history.

Do not rely on a globally installed `argus` on Linux. In a new shell, use
`$HOME/Argus/.venv/bin/argus` (or activate that venv explicitly). If venv
creation reports that `ensurepip` is unavailable, install the distribution's
`python3-venv` package and rerun the command.

### Backend notes

Use `copilot`, `pi`, `codex`, `claude`, `opencode`, `grok`, `qoder`, or `dsh`
for `--backend`. Setup adopts a model from the selected CLI's own catalog when
one is available; otherwise it keeps that CLI's native default. It does not
inject an OpenAI model id into Claude Code, Pi, OpenCode, Grok, Qoder, or dsh.
If you have an OpenAI-compatible endpoint, setup installs Pi when needed and
configures it directly:

```bash
ARGUS_SETUP_API_KEY=... argus --setup --non-interactive \
  --api-url https://api.example.com/v1 \
  --api-model model-id
```

For Grok Build, install and authenticate the official xAI CLI first:

```bash
curl -fsSL https://x.ai/cli/install.sh | bash
grok login
argus --setup --non-interactive --backend grok
```

`XAI_API_KEY` is also supported for headless environments. Argus uses Grok's
native headless JSON stream, resumes sessions by ID, and keeps role prompts out
of process arguments.
In PowerShell, use a backtick instead of `\` for line continuation.

#### Choosing a provider on the multi-provider CLIs

Pi and OpenCode are provider-agnostic fronts: which account they bill depends on
what you authenticated them against (a native DeepSeek key, Anthropic, Azure, a
local vLLM, a Copilot proxy). Argus passes your configured model id straight
through, so a bare id like `deepseek-chat` is resolved by the CLI itself.

Name the provider when a bare id is ambiguous or when the CLI requires it:

```bash
# Pi — only needed when two authenticated catalogs carry the same model id
export ARGUS_SKILL_PI_PROVIDER=deepseek

# OpenCode — required: `opencode run --model` only accepts provider/id
export ARGUS_SKILL_OPENCODE_PROVIDER=deepseek
```

Both are also settable from the cockpit `/config` view, and persist across
restarts once set there.

`argus --doctor` reads the CLI's authenticated catalog and tells you when the
configured provider is not one you hold a key for, or when a model id you
selected is not on offer.

Use `argus --config-help` to inspect each role's effective model and where it
came from. Catalog commands are backend-specific, for example
`pi --list-models`, `opencode auth list`, and `qodercli --list-models`.

Full details, including the breaking change for Pi deployments that relied on
the old implicit `github-copilot` prefix: **[backend providers](docs/backend-providers.md)**.

### Launch

Windows and macOS can use `argus` after PATH setup. On Linux, replace `argus`
below with `$HOME/Argus/.venv/bin/argus` unless the venv is active.

```bash
argus
```

```bash
argus doctor                         # Agent-driven inspection and repair
argus doctor --advisor none --verify # deterministic verification, no model call
argus --status                       # inspect the current runtime
```

## Interfaces

### Windows Desktop

The Windows x64 source tree includes an Electron host that supervises a frozen
copy of the same Argus runtime and opens the existing Web cockpit—there is no
separate Desktop fork of Manager, Workbench, or the WebAPI. Source setup,
security boundaries, verification, and packaging commands are documented in
**[Windows Desktop](docs/windows-desktop.md)**.

### Terminal cockpit

```bash
argus
```

Use the terminal cockpit to talk to the Manager, follow live work, inspect state, and resume projects.
Without an explicit `--port`, Argus reuses a compatible backend or selects the
first available port starting at `8799` when another program or stale backend
occupies it. On Windows, a plain `argus` launch also opens the Web UI; use
`argus --no-open` for the terminal cockpit only.

### Web UI

Start Argus and open the Web UI in your default browser:

```bash
argus --web
```

Preferred address: [http://127.0.0.1:8799](http://127.0.0.1:8799); Argus advances
to the next available port when needed.

The Web UI follows the browser language on first launch and supports English
and Simplified Chinese. Use the language button in the session sidebar to
switch; the selection is saved in the browser.

```bash
argus --web --web-port 8800  # use another port
```

#### Remote server over SSH

On the server:

```bash
argus --web
```

On your computer:

```bash
ssh -L 8799:127.0.0.1:8799 user@server
```

Then open [http://127.0.0.1:8799](http://127.0.0.1:8799) locally.

<details>
<summary><strong>Direct LAN access</strong></summary>

A non-loopback bind is always protected by a bearer token. If
`ARGUS_SKILL_WEB_TOKEN` is set it is used; otherwise one is minted for that run:

```bash
argus --web --web-host 0.0.0.0 --web-port 8799
```

This prints the address other devices can reach, the token, and a QR code.
Set the token yourself to keep one across restarts:

```bash
export ARGUS_SKILL_WEB_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

To serve without a token — only behind your own authenticating proxy — set
`ARGUS_SKILL_WEB_ALLOW_INSECURE=1`.

</details>

### From a phone

Telegram, Feishu/Lark, and the web UI all work from a phone. The two chat bots
dial out, so a daemon behind NAT needs no tunnel and no public URL:

```bash
# Feishu / Lark — WebSocket long connection, no request URL to configure
pip install 'argus-skill[feishu]'
export ARGUS_SKILL_ENABLE_FEISHU=1
export ARGUS_SKILL_FEISHU_APP_ID=cli_xxx ARGUS_SKILL_FEISHU_APP_SECRET=xxx

# Telegram
export ARGUS_SKILL_ENABLE_TELEGRAM=1
export ARGUS_SKILL_TELEGRAM_BOT_TOKEN=... ARGUS_SKILL_TELEGRAM_CHAT_ID=...
```

Both bots serve the same commands (`/add`, `/status`, `/nudge`, `/backlog`, …).
The web UI is installable to the home screen and pairs by scanning the QR code
printed by `argus --web --web-host 0.0.0.0`.

See **[docs/mobile.md](docs/mobile.md)** for the full setup.

## Advanced usage

Argus is designed to be changed, not merely configured.

### Autonomy level

The default `pragmatic` mode handles recoverable engineering choices—timeouts, failed tests, benchmark sizing, and technical routes—without interrupting you. It asks only for credentials, more spending, irreversible/outward-facing actions, or changes to an operator-owned acceptance boundary.

```bash
export ARGUS_SKILL_AUTONOMY_MODE=cautious    # ask on every explicit question
export ARGUS_SKILL_AUTONOMY_MODE=pragmatic   # default: recover technical issues
export ARGUS_SKILL_AUTONOMY_MODE=autonomous  # maximize reversible execution
```

The Web configuration view and `/config` expose the same setting.

### Adapt the runtime

If you are an agent enthusiast, deploy Argus locally and make the complete loop fit the way you work. Tune role prompts, workflow boundaries, review policy, tools, and operating conventions; connect your own infrastructure; preserve the behavior you care about with tests.

One worked engineering case is **[exploration without local hill climbing](docs/exploration-without-local-hill-climbing.md)**: how report-only research, high-risk mechanism portfolios, single-run screening, and strict final claims were separated after an MI300X serving campaign exposed overly conservative incentives.

For the wider set, **[what goes wrong and what we did about it](docs/failure-modes-and-fixes.md)** records six real failure modes — an experiment that measured its own token cap, world knowledge frozen at the training cut-off, settling for finishing rather than achieving something, ceremony applied before an idea had earned it, local hill climbing, and a reluctance to report a win. It also records a fix we got wrong: adding a gate to enforce ambition, when the honest answer was that defensive checks do not produce good work, they produce work that passes checks.

### Build your own Vertical

A Vertical gives your field its own stages, Skills, datasets, tools, evidence expectations, evaluation methods, and completion criteria. Planning and review can then follow the real standards of your domain instead of a generic process.

The `math` vertical is the worked example: three stages, a content-addressed
evidence store, Lean-backed mechanical verification, and an explicit rule for
which kind of check is allowed to settle which kind of question. See
**[mathematical research](docs/research-mathematics.md)**.

### Use another agent as the outer layer

GitHub Copilot, Pi, Codex, Claude Code, OpenCode, Grok Build, OpenClaw, or Hermes can be the environment from which you invoke Argus, inspect its state, operate its local CLI or Web/API surface, and continue improving the deployment.

- **Native Argus backends:** GitHub Copilot CLI, Pi, Codex CLI, Claude Code, OpenCode, Grok Build, Qoder, DeepSeek Harness
- **External agent operators:** OpenClaw, Hermes, or any agent that can use a shell or HTTP API

For durable missions, install or adapt the portable
[`argus-runtime-orchestration` Agent Skill](integrations/agent-skills/argus-runtime-orchestration/SKILL.md).
It defines the two-party operator model, the active `Needs you` intervention loop,
host-specific adapters, evidence boundaries, and closeout checks.

Useful entry points:

```bash
argus doctor
argus --status
argus --web
```

The most capable setup is often an Argus instance deliberately adapted to your own ambitious field and way of working.

## Update

Windows:

```powershell
py -m pip install --upgrade --force-reinstall "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
$Argus = Join-Path (py -c "import sysconfig; print(sysconfig.get_path('scripts'))") "argus.exe"
& $Argus --version
& $Argus doctor --advisor none --verify
```

macOS:

```bash
uv tool install --force --python 3.12 \
  "argus-skill @ https://github.com/lbx154/Argus/archive/refs/heads/main.zip"
"$(uv tool dir --bin)/argus" --version
"$(uv tool dir --bin)/argus" doctor --advisor none --verify
```

Linux source checkout:

```bash
"$HOME/Argus/.venv/bin/argus" update
"$HOME/Argus/.venv/bin/argus" --version
"$HOME/Argus/.venv/bin/argus" doctor --advisor none --verify
```

The Linux source command refuses dirty or detached checkouts, fast-forwards the
configured upstream, and refreshes the editable installation when the revision
changes. Argus detects stale local WebAPI and daemon processes and replaces them
at a controlled task boundary. Update verification is deterministic and does
not spend a model call.

## Uninstall

```powershell
# Windows
py -m pip uninstall argus-skill
```

```bash
# macOS
uv tool uninstall argus-skill
```

On Linux, stop Argus, preserve any work you need, then remove the
`$HOME/Argus` checkout and its `.venv`. Package removal intentionally leaves
runtime state under `$HOME/.argus-skill` untouched on every platform; delete
that directory only when you also want to remove projects, configuration, and
logs.

## Installation troubleshooting

- Confirm which executable the shell is using: `Get-Command argus -All` on
  PowerShell, or `type -a argus` on macOS/Linux. Its `argus --version` release
  id should change after an update.
- On macOS, use `"$(uv tool dir --bin)/argus"` immediately. Run
  `uv tool update-shell` once and open a new terminal for plain `argus`.
- On Windows, recover the exact Scripts directory with
  `$Scripts = py -c "import sysconfig; print(sysconfig.get_path('scripts'))"`.
  Add it to the current window with `$env:Path = "$Scripts;$env:Path"`. For new
  windows, use the Python installer’s **Modify** action and enable
  **Add Python to PATH** rather than creating a venv.
- On Linux, use `$HOME/Argus/.venv/bin/argus`; a global `argus` may be an older
  installation. Install `python3-venv` if `python3 -m venv` lacks `ensurepip`.
- Use `argus doctor --advisor none --verify` for deterministic diagnostics.
  Use `argus doctor` when you want an installed Agent to inspect and repair
  Argus directly.
- Use `argus --config-help` to check the effective backend/model before blaming
  setup or authentication.

## What Argus has done so far

A partial record, grouped by **who decides whether the result counts** — and none of
those deciders is Argus.

### Open code

| Repository | Result |
|---|---|
| **[ace-2](https://github.com/Argus-AiTeam/ace-2)** | A Qwen2.5-0.5B W4A8 inference accelerator with no human author of record for its spec, RTL, verification, or physical flow. **13,914/13,914** runtime commands; SKY130 at **0.614 mm²** (cap 2.0), **+0.6966 ns** slack, WNS/TNS 0.00 ns, 100 MHz. The certificate publishes its own exclusions: no DRC/LVS, no GDS, no silicon. |
| **[minimax-h3-mac](https://github.com/Argus-AiTeam/minimax-h3-mac)** · **[-desktop](https://github.com/Argus-AiTeam/minimax-h3-desktop)** · **[ComfyUI nodes](https://github.com/Argus-AiTeam/ComfyUI-MiniMax-H3-MLX)** | A ~62 GiB model, not shrunk but run: **47 min 58.7 s** at **~15.8 GB** peak on a 24 GB M4 Pro. On one RTX A6000, Turbo 8-step reaches **6.159×** over a BF16 N=10 baseline. Candidates that failed the quality gate are published as *rejected*. |
| **[FlashDA](https://github.com/SJTU-DENG-Lab/FlashDA/tree/feature/dllm-fa4-adaptation)** · **[Diffulex](https://github.com/SJTU-DENG-Lab/Diffulex)** | Six diffusion-LM mask families carried into a **FlashAttention-4 CuTe DSL** kernel in **21.85 hours** of model compute, under **80 CNY**, with 2 interruptions in 87.7 hours. **19/19** parity across SM80/SM90; on H200/SM90 it reaches **92–95% of native FA4** and **1.61–2.57×** over the Diffulex Triton backend, both sides CUDA-Graph captured. The early route was **4.9–29.6× slower** than native; recognising the data path was wrong and abandoning it is what produced the result, and that rejected route is retained with its evidence. |

FlashDA builds on the excellent FlashAttention-4 / CuTe DSL work by
[Tri Dao](https://github.com/tridao) and collaborators. Reproductions and ports beyond
SM90 are very welcome; the full protocol and per-scenario latencies are in
[`EXPERIMENT_RESULTS.md`](https://github.com/SJTU-DENG-Lab/FlashDA/blob/feature/dllm-fa4-adaptation/EXPERIMENT_RESULTS.md).

### Judged by outside maintainers

| Submission | Outcome |
|---|---|
| **[sglang#35038](https://github.com/sgl-project/sglang/pull/35038)** — native SenseNova U1 multimodal generation and interleave serving | 36 files, **+11,263/−72**, 14 commits. 1,116 tensors, 0 missing; VQA exact **160/160**; concurrency-8 exact **8/8**; **5.108×** at BS8. One engineer with a turn-by-turn agent invested **60+ hours** without completing it; Argus finished inside **24.14 hours**. *Open.* |
| **[fla-org#1045](https://github.com/fla-org/flash-linear-attention/pull/1045)** — TileLang RWKV6 backend | **Merged**, 1.21× fwd+bwd on H100 NVL, no inline change requests. Its description states the work was completed autonomously — and an outside maintainer accepted that sentence with the code. |
| **[fla-org#1109](https://github.com/fla-org/flash-linear-attention/pull/1109)** — SM100 autotune crash | **Merged.** Two lines, no speedup: before the fix the test file could not finish; after it, **76 tests pass**. |
| **[fla-org#1128](https://github.com/fla-org/flash-linear-attention/pull/1128)** · **[#1114](https://github.com/fla-org/flash-linear-attention/pull/1114)** | KDA training 1.29× over Triton, and `AttnRes` 1.102× geomean on B200. Both report their *worst* row alongside the mean, and #1128 shipped the 1.078–1.099× number it could verify rather than the 1.541× it measured. *Open.* |

### Scored by official harnesses

| Arena | Result |
|---|---|
| SWE-Bench Pro (731 tasks) | **≈78%** vs **59%** for direct Copilot, same model both sides — and **35** tasks declared `blocked` rather than reported as unsupported successes |
| SOL-ExecBench | Rank **#6 globally**; 7 kernels top-3; beat the #1 entrant on 2 |
| MLE-Bench Lite | **69.2%** medal rate (9/13): 3 gold, 3 silver, 3 bronze, against Kaggle leaderboards |
| AARRI-Bench | **63/82 (76.8%)** vs 68.3% paper best |
| nanochat / nanoGPT speedrun | 0.9636 vs 0.9646 BPB on B200; **79.77 s** vs 80.18 s same-device human record |

### Decided by external checkers

- **MOF generation** — chemical control 92.5 / 100.0 / 74.5%, AUC 0.594 → 0.833, verified by external `MOFChecker`. The admitted method is *smaller* than the one it replaces.
- **Erdős–Gyárfás** — six proof-backed frontier updates, with one falsified route retained as evidence rather than deleted.
- **Research writing** — six paper pipelines to submission across 254 missions, including 16 Stage rollbacks under review.
- **On itself** — at maturity, **21% fewer tokens** and **15% less active time** per solved SWE-Bench Pro task than at startup, with weights unchanged. Longest single campaign: **8.1 days**.

> [!NOTE]
> Every number is reproduced from the [technical report](https://arxiv.org/pdf/2608.05144)
> or the linked repository, and carries the scope conditions stated there.
