# Argus

**English** · [简体中文](README.zh-CN.md)

## Overview

Argus is an autonomous research and engineering runtime for long-horizon work. It coordinates four persistent AI roles:

- **Manager** — interprets operator intent, selects the workflow, and controls stage transitions.
- **Planner** — decomposes the objective into executable tasks and evidence requirements.
- **Engineer** — implements, researches, runs experiments, and produces artifacts.
- **Reviewer** — independently checks correctness, evidence, limitations, and completion.

Project state, task history, checkpoints, skills, and review evidence are persisted across sessions. Argus supports GitHub Copilot CLI, OpenAI Codex CLI, Claude Code, OpenCode, and Pi backends.

[Technical Report PDF](technical_report/argus-technical-report.pdf)

## Installation

### Requirements

- Python 3.11 or newer
- Node.js 22 or newer
- One supported agent CLI with valid authentication

### Source installation

```bash
git clone https://github.com/lbx154/Argus.git
cd Argus

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

Install and authenticate one backend. For GitHub Copilot:

```bash
npm install -g @github/copilot
copilot login
argus --setup --non-interactive \
  --backend copilot \
  --accept-house-rules
argus
```

For Pi:

```bash
npm install -g --ignore-scripts @earendil-works/pi-coding-agent
pi  # run /login, then exit Pi after authentication
argus --setup --non-interactive \
  --backend pi \
  --accept-house-rules
argus
```

Other supported backend installers:

```bash
npm install -g @openai/codex@latest
npm install -g @anthropic-ai/claude-code
curl -fsSL https://opencode.ai/install | bash
```

### npm beta installation

```bash
npm install -g @github/copilot
copilot login
npm install -g @argusevolve/argus@beta
argus --setup --non-interactive \
  --backend copilot \
  --accept-house-rules
argus
```

### Update a source installation

```bash
cd Argus
git pull --ff-only
. .venv/bin/activate
pip install -e .
argus
```
