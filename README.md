<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/svg/argus-logo-horizontal-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/brand/svg/argus-logo-horizontal.svg">
  <img src="docs/assets/brand/svg/argus-logo-horizontal.svg" width="420" alt="Argus">
</picture>

### Persistent, reviewed autonomy for research and engineering

Long-running agent work that can plan, execute, verify, pause, and continue beyond a single model turn.

**Preview v0.1.1 · Official open-source release on the way.**

[![GitHub Stars](https://img.shields.io/github/stars/lbx154/Argus?style=flat-square)](https://github.com/lbx154/Argus/stargazers)
[![License](https://img.shields.io/github/license/lbx154/Argus?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![arXiv](https://img.shields.io/badge/arXiv-2608.05144-b31b1b?style=flat-square&logo=arxiv&logoColor=white)](https://arxiv.org/abs/2608.05144)

[Website](https://argusbot.cn) · [Video Demo](https://www.youtube.com/watch?v=i8Qy9HCboQE) · [Technical Report · arXiv:2608.05144](https://arxiv.org/pdf/2608.05144) · [WeChat Community](#wechat-community) · **English** / [简体中文](README.zh-CN.md)

`Manager` → `Planner` → `Engineer` ⇄ `Reviewer`

</div>

---

## What is Argus?

Most agents are optimized for one conversation or one coding turn. Argus is built for work that lasts: it keeps state, separates execution from judgment, and resumes from verified progress instead of starting over.

| Capability | What it means |
|---|---|
| **Persistent state** | Tasks, checkpoints, decisions, Skills, and evidence survive sessions and runtime upgrades. |
| **Independent review** | Execution and verification stay separate; normal rounds end with a Reviewer judgment. |
| **Four-role runtime** | Manager, Planner, Engineer, and Reviewer have distinct authority and responsibilities. |
| **Real tool use** | Agents work through files, terminals, experiments, APIs, and inspectable artifacts. |
| **Domain extensibility** | Verticals can define custom stages, tools, evidence requirements, and completion standards. |
| **Multiple backends** | Run with GitHub Copilot CLI, Pi, Codex CLI, Claude Code, OpenCode, or Grok Build. |

## Runtime model

| | Authority | Responsibility |
|---:|---|---|
| `01` | **Manager · Control** | Interprets operator intent, selects the workflow, and owns stage transitions. |
| `02` | **Planner · Direction** | Chooses the next high-value task and defines the evidence it must produce. |
| `03` | **Engineer · Execution** | Implements, researches, runs experiments, and creates inspectable artifacts. |
| `04` | **Reviewer · Verification** | Independently checks correctness, evidence, limitations, and completion. |

A project can stop, resume, survive a runtime replacement, and continue from its latest verified position.

**Native backends:** `GitHub Copilot CLI` · `Pi` · `OpenAI Codex CLI` · `Claude Code` · `OpenCode` · `Grok Build`

## Quick Install

### Requirements

- Python 3.11+
- Node.js 22+
- One supported Agent CLI installed and authenticated through its official login flow

### 🚀 Agent-assisted installation (recommended)

> [!TIP]
> **Skip the manual installation steps.** Send the complete prompt below to
> Codex CLI, Claude Code, GitHub Copilot CLI, Pi, OpenCode, or Grok Build. The agent will
> inspect the environment, install Argus, connect the current backend, and
> verify it with `argus --doctor`.

```text
Read https://github.com/lbx154/Argus/blob/main/docs/agent-install.md and follow
it to install and configure Argus on my machine. Prefer the Agent CLI currently
running this conversation as the Argus backend. Perform the environment checks,
installation, configuration, and argus --doctor verification. Before account
login, sudo, global configuration changes, or any other action requiring human
authorization, explain why and wait for my approval. Never ask me to paste a
password, access token, or API key into the conversation.
```

The agent will follow the **[installation execution contract](docs/agent-install.md)**.

### Install

```bash
git clone https://github.com/lbx154/Argus.git
cd Argus

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

### Connect a backend

```bash
argus --setup --non-interactive \
  --backend copilot \
  --accept-house-rules
```

Use `copilot`, `pi`, `codex`, `claude`, `opencode`, or `grok` for `--backend`.

For Grok Build, install and authenticate the official xAI CLI first:

```bash
curl -fsSL https://x.ai/cli/install.sh | bash
grok login
argus --setup --non-interactive --backend grok --accept-house-rules
```

`XAI_API_KEY` is also supported for headless environments. Argus uses Grok's
native headless JSON stream, resumes sessions by ID, and keeps role prompts out
of process arguments.

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

Full details, including the breaking change for Pi deployments that relied on
the old implicit `github-copilot` prefix: **[backend providers](docs/backend-providers.md)**.

### Launch

```bash
argus
```

```bash
argus --doctor   # verify the installation
argus --status   # inspect the current runtime
```

### Codex / Claude Code plugin

One-command installation and usage: [docs/plugin.md](docs/plugin.md).

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
argus --web --no-open    # start without opening a browser
argus --web --port 8800  # use another port
```

#### Remote server over SSH

On the server:

```bash
argus --web --no-open
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
argus --web --host 0.0.0.0 --port 8799 --no-open
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
printed by `argus --web --host 0.0.0.0`.

See **[docs/mobile.md](docs/mobile.md)** for the full setup.

## Advanced usage

Argus is designed to be changed, not merely configured.

### Adapt the runtime

If you are an agent enthusiast, deploy Argus locally and make the complete loop fit the way you work. Tune role prompts, workflow boundaries, review policy, tools, and operating conventions; connect your own infrastructure; preserve the behavior you care about with tests.

### Build your own Vertical

A Vertical gives your field its own stages, Skills, datasets, tools, evidence expectations, evaluation methods, and completion criteria. Planning and review can then follow the real standards of your domain instead of a generic process.

### Use another agent as the outer layer

GitHub Copilot, Pi, Codex, Claude Code, OpenCode, Grok Build, OpenClaw, or Hermes can be the environment from which you invoke Argus, inspect its state, operate its local CLI or Web/API surface, and continue improving the deployment.

- **Native Argus backends:** GitHub Copilot CLI, Pi, Codex CLI, Claude Code, OpenCode, Grok Build
- **External agent operators:** OpenClaw, Hermes, or any agent that can use a shell or HTTP API

Useful entry points:

```bash
argus --doctor
argus --status
argus --web --no-open
```

The most capable setup is often an Argus instance deliberately adapted to your own ambitious field and way of working.

## Update

```bash
argus update
```

The command refuses dirty or detached checkouts, fast-forwards the configured
upstream, and refreshes the editable installation when the revision changes.
Run `argus` afterward; it detects stale local WebAPI and daemon processes and
replaces them at a controlled task boundary.

## WeChat community

Scan the QR code below to join the Argus community. The expiry date is printed in the image; if it has expired, open an Issue and ask the maintainers for the latest code.

<p align="center">
  <img src="docs/assets/argus-wechat-group.jpg" width="360" alt="Argus WeChat community QR code">
</p>
