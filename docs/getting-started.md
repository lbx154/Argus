# Getting started with Argus

This guide takes you from an empty machine to a finished first task. It covers
three ways to work: the command line, the web UI, and the desktop app. Every
command below was checked against the `argus` CLI in this checkout
(`argus 0.1.8`); the example runs are real projects, with their times and costs
taken from their own logs.

Companion guides: [best practices](best-practices.md) (objectives, changing
direction mid-run, models and spend) and
[building a vertical](building-a-vertical.md).

## What you are installing

Argus is a Python package plus a bundled terminal cockpit (Node.js) and a web
UI. It does not call a model API itself; it drives one of the coding-agent CLIs
you already use (Copilot, Codex, Claude Code, Cursor, Pi, OpenCode, Grok, Qoder,
DeepSeek Harness) and lets four roles share it: a Manager that routes your
message, a Planner that decides the next task, an Engineer that does the work,
and a read-only Reviewer that decides whether the work is done.

Two things follow from that design and explain most of what you will see:

- **A background worker does the work.** Your cockpit, browser tab or terminal
  can close; the project's daemon keeps running and keeps its state under
  `~/.argus-skill/projects/<id>/`.
- **Nothing is "done" until the Reviewer says so.** A task usually takes
  several Engineer rounds. Expect the first small task to take minutes, not
  seconds.

## Prerequisites

| Requirement | Why |
|---|---|
| Python 3.11+ | the `argus` package (`pyproject.toml` requires `>=3.11`) |
| Node.js 22.12+ | the terminal cockpit is an Ink app; `argus` refuses to start it on older Node (`argus: Ink TUI requires Node.js 22.12 or newer`) |
| One authenticated agent CLI | Argus reuses its login; there is no separate Argus account |

Install and log into one CLI first. The README's
[Quick Install](../README.md#quick-install) table lists the install and login
command for each backend; for example GitHub Copilot CLI is
`npm install -g @github/copilot` then `copilot login`.

## Install

The commands are the README's, repeated here so this page stands alone.

Linux (isolated venv; use this on servers):

```bash
git clone https://github.com/microsoft/ArgusAgent.git "$HOME/Argus"
cd "$HOME/Argus"
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
ARGUS_BIN="$HOME/Argus/.venv/bin/argus"
"$ARGUS_BIN" --version
```

Windows (PowerShell, no venv):

```powershell
py -m pip install --upgrade pip
py -m pip install --upgrade --force-reinstall "argus @ https://github.com/microsoft/ArgusAgent/archive/refs/heads/main.zip"
```

macOS (managed command):

```bash
uv tool install --force --python 3.12 "argus @ https://github.com/microsoft/ArgusAgent/archive/refs/heads/main.zip"
```

On Linux, `argus` below means `$HOME/Argus/.venv/bin/argus` unless the venv is
active. Everywhere, `python -m argus ...` runs the same CLI without ever
starting the Node cockpit; it is what you want in scripts and over SSH without
a terminal.

## First-time setup and a health check

```bash
argus --setup
argus doctor
```

`argus --setup` is an interactive wizard: it asks which backend to use, checks
that the CLI is installed and logged in, runs one real model turn through it,
and only then saves the profile. It ends with `Setup complete. Run `argus`.`
If you would rather not answer prompts:

```bash
argus --setup --backend copilot --non-interactive
```

Two variants worth knowing:

| Situation | Command |
|---|---|
| Keep Argus on its own Copilot account, separate from your interactive login | `argus --setup --backend copilot --copilot-home "$HOME/.copilot-argus" --copilot-login` |
| Use an OpenAI-compatible endpoint (served through Pi) | `argus --setup --api-url URL --api-model MODEL` with the key in `ARGUS_SETUP_API_KEY` rather than `--api-key`, so it stays out of shell history |

`argus doctor` is read-only. This is what it printed on the machine this guide
was written on (a source checkout, Pi backend):

```
argus doctor — cross-platform diagnostics

✓ ARGUS-HOST-001 [host/supported_host] Linux 6.8.0-139-generic x86_64
✓ ARGUS-INSTALL-001 [install/source_checkout] /data/.../Argus
✓ ARGUS-ASSET-001 [install/assets_ready] release manifest and Web/TUI assets are present
✓ ARGUS-PYTHON-001 [cli/python_ready] 3.12.3 at /data/.../.venv/bin/python
✓ ARGUS-NODE-001 [cli/node_ready] v22.23.2
✓ ARGUS-WEB-001 [web/compatible] 127.0.0.1:8799 is compatible
! ARGUS-DESKTOP-001 [desktop/tauri_dependencies_missing] Tauri Desktop sources exist but npm/Rust build dependencies are incomplete
    fix: run `npm --prefix desktop-tauri ci` and install the Rust Windows toolchain
✓ ARGUS-DAEMON-001 [daemon/stopped] no daemon is running
✓ ARGUS-BACKEND-001 [backend/ready] pi 0.85.1 runnable at /home/.../argus-pi (subscription_cli; authentication checked; ...)

all blocking checks passed
```

A `!` line is advice, not a failure; the desktop warning above only matters if
you intend to build the desktop app from this checkout. When something is
wrong, `argus doctor --fix-safe` applies the repairs the doctor itself marked
safe and reruns; `argus doctor --advisor auto` lets one of the installed agent
CLIs inspect and repair. Without one of those two options `doctor` changes
nothing.

A note on help: `argus --help` (and `argus doctor --help`,
`argus verticals --help`) print the same short overview. The complete flag
reference is behind an environment variable:

```bash
ARGUS_SKILL_DEBUG_HELP=1 argus --help
```

## Path 1: the command line

There are two ways to use the CLI. A bare `argus` opens the terminal cockpit,
where you type in natural language. Everything else (`--daemon`, `--status`,
`--notify`, ...) is a one-shot command that talks to the project's worker and
exits; those work over SSH, in cron, and without Node.

### Which project a command means

Argus keys a project's state on the directory you run it from. Management
commands attach to the newest session for the current directory, or to the
one you name:

| Flag | Meaning |
|---|---|
| (none) | the newest session for the current working directory |
| `--project-root DIR` | the newest session for `DIR` |
| `--resume ID` | a specific session id (with no id, a picker of recent sessions) |
| `--continue` | the most recently active session |
| `--life-dir DIR` | use `DIR` instead of `~/.argus-skill` as the state root (`ARGUS_SKILL_HOME` does the same) |

### Start a task without opening the cockpit

Write the objective in a file, `cd` into the directory the work should happen
in, and start a background worker with a campaign objective:

```bash
cd ~/work/matmul-roofline
argus --daemon --continuous --objective-file objective.txt --bounded --backend copilot
```

The pieces:

| Flag | What it does and why you would set it |
|---|---|
| `--daemon` | start a detached worker (`--daemon-fg` keeps it in the foreground, for systemd or debugging) |
| `--continuous` | give the worker an objective and let the Planner keep generating tasks toward it; refused without an objective (`--continuous requires a non-empty --objective`) |
| `--objective TEXT` / `--objective-file PATH` | the objective; the file form keeps long text and quotes out of the shell (`--objective` without `--continuous` is refused too) |
| `--bounded` | stop when the Planner certifies the project done; without it the worker keeps generating work for the same objective |
| `--backend NAME` | the agent CLI for this daemon; it outranks the persisted setup for this launch and is exported to every role |
| `--mission-width N` | how many tasks may run in parallel (default 2; `1` is serial) |

Before the worker starts, Argus re-checks that the backend is installed and
logged in; if not, it prints the readiness report and exits with code 3 instead
of starting a worker that cannot call a model.

### A real first campaign

The objective below started the campaign that project `s-02d3c282` ran on this
machine on 2026-09-30 (`objective-roofline.txt`, quoted in full because its
shape is what makes the rest work):

> 做一项小规模但真实的研究：在本机 4 块 NVIDIA RTX A6000 上，PyTorch 方阵乘法的实际吞吐（TFLOPS）随矩阵规模（512 到 16384）和精度（FP32、TF32、BF16、FP16）如何变化，与理论峰值的差距能否用一个简单的 roofline 式模型（计算强度与显存带宽）解释。要求：真实实验、每种配置多次重复取中位数并报告离散度，拟合模型并给出误差，画图，最后写成一篇 4 页以内的短论文（含方法、结果、讨论、复现说明），并通过内部评审。所有数字必须来自本机真实运行，不允许估算或编造。

It was launched with `--backend copilot --mission-width 1` (the live process
still shows those flags). About two hours in, `argus --status` reported:

```
  project  : .../projects/s-02d3c282
  daemon   : alive (pid 1430124, up 1h 48m, backend live — see /roles, width 1)
  budget   : global daily $1000.00 (spent $30.71) · remaining $969.29
  active   : 0 pending · 1 running · 0 paused
  current  :
    title    : 重绘正式数据图并完成论文稿
  history  : 2 done
  cost     : $29.79 cumulative
  continuous: on
  pipeline : vertical=research
  lifecycle:
    state         : writing
```

By then the work directory held `paper/main.tex`, `paper/results.tex`,
`results/attempt-01`, `figures/` and `src/`; `paper/results.tex` reports, at
n=16384, 22.81 / 57.01 / 121.11 / 115.96 TFLOP/s for FP32 / TF32 / BF16 / FP16
(58.95 / 73.66 / 78.24 / 74.91 % of the spec sheet) over 17,280 samples, and a
fitted roofline whose calibrated form has a 53.92 % mean absolute error on the
confirmation set. The campaign was still running when this page was written
(its log had a `paper` round returned as `replan_requested`, which is normal:
the Reviewer sent the draft back), so treat those as a snapshot, not a result.

For a small first task, a bounded objective finishes in minutes. Project
`s-4a34a674` on the same machine was started the same way with this objective:

> 用 numpy 数值求解一维阻尼谐振子 x'' + 2γx' + ω0² x = 0（取 ω0=2π, 分别取欠阻尼 γ=0.5、临界 γ=2π、过阻尼 γ=10），与解析解逐点比较给出最大误差，画出三条曲线，把方法、数值结果表和图写进本目录的 README.md。所有数字必须来自真实运行。

Its log runs from 08:11:39Z to 08:23:33Z (12 minutes), one task in two
attempts, `continuous.json` ends with `"done_reason": "planner declared project
done"`, and it cost $0.91 (12 model calls in `usage.jsonl`). The README it
wrote gives maximum errors of 6.0e-07, 1.6e-07 and 5.1e-07 for the three
damping cases against the analytic solution.

### Watch, steer, stop

| Command | What it does |
|---|---|
| `argus --status` | one screen: daemon, budget, current task, stage, last events |
| `argus --follow` | stream the event log to the terminal (`tail -f` style, Ctrl-C to stop) |
| `argus --watch` | the read-only live cockpit |
| `argus --notify "text"` | queue guidance for the next Engineer round (`--notify-stage STAGE` holds it until that stage) |
| `argus --answer "text"` | answer the question a paused task is waiting on (`--answer-item ID` when several wait) |
| `argus --ask "question"` | ask the Manager something and exit; nothing is queued and no daemon is needed |
| `argus --daemon-stop --drain` | let the current task finish at a clean boundary, then exit; the safe way to stop before upgrading |
| `argus --daemon-stop --force` | SIGKILL if it does not exit in time (interrupts running work) |
| `argus --daemon-runbook` | print the restart playbook for the current project |

The difference between `--notify` and `--answer` matters: a nudge is read at
the next round and does not wake a task that has stopped to ask you something;
`--answer` does, and it also rewrites the task so the next round reads your
answer instead of re-asking. [Best practices](best-practices.md#changing-direction-while-it-runs)
goes into this.

### The terminal cockpit

```bash
argus
```

The cockpit needs a real terminal (piped or cron use gets a message pointing
you to `--web`, `--watch`, `--status` or `--daemon`). Type what you want in
plain language. The Manager reads every message and decides whether to answer
it itself (a question, a status request, a small local check) or to turn it
into a task for the Planner, Engineer and Reviewer (multi-step work, anything
that needs an independent review). You do not choose a vertical, a model or a
backend for the first task; the Manager picks the vertical from your text and
the backend comes from setup.

`/new` (optionally `/new <objective>`) opens a two-field form, Name and
Objective (Tab or arrows switch fields, Enter creates), and switches the
cockpit to the new session; its worker starts when the first task arrives.

Commands you will use in the first hour (`/help` lists them all):

| Command | Purpose |
|---|---|
| `/task <text>` | queue work directly, skipping the "is this a chat?" decision |
| `/ask <question>` | answer inline; no task is queued |
| `/plan <objective>` | preview the Planner's execution plan before committing |
| `/status`, `/roles`, `/backlog`, `/journal` | what is running, on which backend/model, what is queued, what happened |
| `/nudge <text>` | inject guidance into the running task |
| `/abort` | stop the running task now |
| `/backend [name]`, `/config [key=value]` | view or change the runner backend and runtime settings (persisted) |
| `/resume`, `/daemons` | switch to another project |
| `/quit` | leave; background work keeps running |

## Path 2: the web UI

```bash
argus --web
```

From the `argus` command this goes through the cockpit launcher, which picks
the first free port from 8799 and opens your browser at
`http://127.0.0.1:8799`. With an explicit host or port, or through
`python -m argus --web`, the Python server runs directly and does not open a
browser:

```bash
argus --web --web-port 8800
python -m argus --web            # over SSH: then `ssh -L 8799:127.0.0.1:8799 user@server`
```

Binding to a LAN address (`--web-host 0.0.0.0`) always requires a bearer
token: `ARGUS_SKILL_WEB_TOKEN` if set, otherwise one is minted for the run and
printed with a QR code. The web assets are checked into the repository and
packaged with the wheel, so nothing needs building first.

### The first screen

With no sessions yet the page shows **Start a project**, the line *No sessions
yet. Create one to begin.*, and a **New project** button. The button creates an
idle session (no form; the toast reads *New session ready. Say what it is for
and the work begins.*). Then type the objective into the message box. The
message goes to `POST /api/projects/{sid}/message`, the Manager classifies it,
and if it is work the session's daemon is started on demand and the task is
queued. The web UI follows your browser language (English or Simplified
Chinese); the sidebar has a language button.

### A real first task from the browser

Project `s-67fb6d62` in the fresh-install trial on this machine was created
from the web UI (`session.json` records `origin: web`). Its first message, at
06:57:51Z:

> 帮我在这台机器的 GPU 上测一下 PyTorch 矩阵乘在 fp16 和 fp32 下的 TFLOPS，写个脚本跑出真实数字，整理成表格。

The reply, three minutes later at 07:02:21Z, reported an average of 121.31
TFLOPS in FP16 and 23.96 in strict FP32 at 16384×16384 across the four GPUs,
with a per-GPU table and the script name (`benchmark_torch_matmul.py`, seven
timed batches, medians). A follow-up at 07:03:44Z, "现在项目里都有什么文件？把 bf16
也补测一下，更新表格。", produced an updated table at 07:07:00Z (FP16 122.08,
BF16 125.00, FP32 23.87 on average). The project's usage ledger shows $0.80 for
the whole exchange, plus two early calls that were never priced (see below).

That first message was actually sent twice. The first attempt (also 06:57:51Z)
came back as `[not dispatched] Manager could not classify this message
(refused before start: unresolved provider cost ...)`: on a fresh Copilot
install the first call's price was not yet known, and the default policy
refuses to spend money it cannot price. [Best practices](best-practices.md#controlling-spend)
explains the setting (`ARGUS_SKILL_UNPRICED_COST_POLICY`) and the trade-off.

### The endpoints behind the UI

If you script against the server, these are the calls the UI itself makes
(all need the bearer token when one is configured):

| Endpoint | Body | Purpose |
|---|---|---|
| `POST /api/daemons` | `objective`, `name`, `workdir`, `launch_cwd` | create a session (an objective starts its worker immediately) |
| `POST /api/projects/{sid}/message` | `text`, optional `route_override` (`auto`/`chat`/`task`), `attachments` | send a message through the Manager; `/message/stream` is the SSE twin |
| `POST /api/projects/{sid}/tasks` | `text`, `autostart_daemon` (default true) | queue work directly |
| `POST /api/projects/{sid}/nudge` | `text` | guidance for the next round (429 when the queue is full) |
| `POST /api/projects/{sid}/backlog/{item_id}/answer` | `text` | answer a paused question and resume |
| `POST /api/projects/{sid}/continuous` | `enabled`, `objective` | turn a campaign objective on or off |
| `GET /api/projects/costs` | | spend per project |

## Path 3: the desktop app

The desktop app is a Tauri host around a frozen copy of the same Python
backend; it opens the same web cockpit. It is a packaged preview channel,
separate from source updates.

- **Download:** the Releases page of
  [lbx154/Argus](https://github.com/lbx154/Argus/releases). Windows ships as
  an NSIS installer named `Argus-<version>-setup.exe`. The release workflow can
  also build macOS (`.dmg`, arm64 and x86_64) and Linux (`.AppImage`, `.deb`)
  packages, but the workflow file notes that v0.1.7 and v0.1.8 were published
  for Windows only, so check the assets of the release you pick.
  `microsoft/ArgusAgent` Releases is a separate channel; do not mix installers
  or updates between the two.
- **What it needs:** nothing else on the machine. The installer bundles the
  backend (`argus-backend`), so no Python, Node.js or venv is required.
- **First launch:** a setup wizard asks you to pick an installed, logged-in
  agent CLI and confirm it; the local backend does not start until you do. If
  you have an internal trial key instead, paste it and click **开始试用** ("start
  trial"): the app downloads the official standalone Copilot program, checks
  its SHA-256, runs one real model reply, and opens the workbench. That path
  needs no CLI, Python, Node or coding-agent account of your own. Either
  choice is remembered; **File → Settings** changes the CLI, executable or
  port later.
- **After that** the app is the web UI from Path 2: create a project, type the
  objective, watch the feed.

To build it from source instead (Python 3.11+, Node 22.12+, Rust stable, plus
MSVC Build Tools on Windows or Xcode command-line tools on macOS), the
documented sequence in [docs/desktop-trial.md](desktop-trial.md) is:

```bash
python -m pip install -e ".[trial]" "pyinstaller>=6.11,<7" tzdata
npm --prefix frontend/web ci
npm --prefix frontend/tui ci
npm --prefix desktop-tauri ci
npm --prefix frontend/web run build && npm --prefix frontend/tui run build
npm --prefix desktop-tauri run build:backend
npm --prefix desktop-tauri run build:unsigned
```

The unsigned bundle lands under `desktop-tauri/src-tauri/target/release/bundle/`.
[docs/windows-desktop.md](windows-desktop.md) covers signing and the update
channel.

## What "finished" looks like

Whichever path you used, the task ends the same way. The Reviewer returns
`done` for the last task; with `--bounded` (or a bounded objective in the
cockpit) the Planner then declares the project done and the worker stops, and
`argus --status` shows `history : N done` with no running task. The evidence is
in the work directory (the oscillator task's `README.md`; the roofline
campaign's `paper/` and `results/`), and the per-call record of what it cost is
in `~/.argus-skill/projects/<id>/usage.jsonl`.

If instead the status shows a task `paused`, read the question with
`argus --status` or in the cockpit and answer it with `argus --answer`; if it
shows `paused_cost`, see [controlling spend](best-practices.md#controlling-spend).

## Where things live

| Path | Contents |
|---|---|
| `~/.argus-skill/` | the state root (`ARGUS_SKILL_HOME` or `--life-dir` overrides it) |
| `~/.argus-skill/config.json` | persisted settings from `--setup`, `/config`, `/backend` |
| `~/.argus-skill/projects/<id>/` | one project's state: `events.jsonl`, `usage.jsonl`, `backlog.jsonl`, `continuous.json`, `daemon.log` |
| `~/.argus-skill/verticals/` | verticals installed from the store |
| your work directory | where the roles read and write files; never inside the state root |
