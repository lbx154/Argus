<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/brand/svg/argus-logo-horizontal-dark.svg">
  <source media="(prefers-color-scheme: light)" srcset="docs/assets/brand/svg/argus-logo-horizontal.svg">
  <img src="docs/assets/brand/svg/argus-logo-horizontal.svg" width="420" alt="Argus">
</picture>

### Persistent, reviewed autonomy for research and engineering

Long-running agent work that can plan, execute, verify, pause, and continue beyond a single model turn.

**Argus v0.1.8**

[![GitHub Stars](https://img.shields.io/github/stars/lbx154/Argus?style=flat-square)](https://github.com/lbx154/Argus/stargazers)
[![License](https://img.shields.io/github/license/lbx154/Argus?style=flat-square)](LICENSE)
[![Node.js](https://img.shields.io/badge/Node.js-22.12%2B-5FA04E?style=flat-square&logo=nodedotjs&logoColor=white)](https://nodejs.org/)
[![arXiv](https://img.shields.io/badge/arXiv-2608.05144-b31b1b?style=flat-square&logo=arxiv&logoColor=white)](https://arxiv.org/abs/2608.05144)

[Website](https://argusbot.cn) · [Getting Started](docs/getting-started.md) · [Video Demo](https://www.youtube.com/watch?v=i8Qy9HCboQE) · [Technical Report · arXiv:2608.05144](https://arxiv.org/pdf/2608.05144) · [WeChat Community](#wechat-community) · **English** / [简体中文](README.zh-CN.md)

`Manager` → (`Planner` →) `Engineer` ⇄ `Reviewer`

</div>

---

> [!IMPORTANT]
> **Repository channels:** **[microsoft/ArgusAgent](https://github.com/microsoft/ArgusAgent)**
> is the official source repository;
> **[lbx154/Argus](https://github.com/lbx154/Argus)** is the development preview.
> Changes reach the official repository through synchronization.

In this repository, **`main` is the stable source branch** and **`dev` is the
development branch**. Target development PRs at `dev`; promote reviewed, validated
changes to `main`. See [Contributing](CONTRIBUTING.md).

## The Driver–Harness Model

A **model** is an engine: it burns compute and puts out tokens. A **harness** is the
drivetrain that couples those tokens to files, shells, compilers, GPUs, and tests. The
**Driver** is the seat — choosing what to do next, judging whether the last result was
any good, and knowing when to stop and ask. In every other agent system that seat holds a
human, which is why the work stops when they go to bed.

**Argus takes the Driver's seat** and splits it across four roles. Every turn a role takes
is a call to a coding-agent CLI you already use; Argus hands the work from role to role,
delivers messages, records what happened, and keeps the permission boundary.

| Role | What it does | Project files |
|---|---|---|
| **Manager** | Your one point of contact. Answers you and does small work itself; hands larger work whole to an Engineer, or to the Planner when one task cannot hold it; reports the result to you. | read and write |
| **Planner** | Lays out work too large for one task as large tasks, each ending in a result you would open and waiting for the tasks whose results it needs; the rest start at once, an Engineer each. Reads each letter as it comes, while the others work, and decides what comes next. | read only |
| **Engineer** | Carries one task through, then sends it to the Reviewer. | read and write |
| **Reviewer** | Opens the result and judges it as you will when you open it: sends back to the Engineer what you would send back, and gives its judgment to whoever gave the task. Reruns the decisive check itself. | read only |

**Every hand-off is a letter.** Only the addressee is structured — the backend CLI holds
the model's final answer to it — and the letter is free text, passed on as written. Its
reader sees the project files and the letter, not the writer's conversation, so a letter
that hands on work is that work's whole brief. You can read every letter; together they
are the project's timeline. When the Manager hands you a result, its letter names the files,
and the conversation shows them as a result card you open. You can write to the Manager at any time, also while the team
works: it answers in its next turn and the team carries on. A role that needs something
only you can decide writes to you, and the work waits for your answer. Argus never decides
whether a task succeeded; the letters say so.

Because the worker cannot grade its own work, nobody has to watch it: across 27 campaigns
and 1,548 hours on the earlier Python runtime it needed a human research decision about **once every 310 hours**, at
**95–99%** duty cycle. Everything else is in the
**[technical report](https://arxiv.org/pdf/2608.05144)**.

**Skills.** Argus ships four skills in [`packages/core/skills/`](packages/core/skills):
`research`, `software`, `argus-maintenance` and `learning`. A skill is a folder whose
`SKILL.md` says in its front matter what kind of work it is for, as Codex and Claude Code write
a skill, and every role's prompt lists them. The Manager and the Engineer (and you) may write new skills and improve existing ones
in `~/.argus/skills/`, where a skill replaces the bundled one of the same name; the web page
shows them under **Knowledge & skills** as they change. A plugin is a skill folder that
holds a `plugin.json` (`name`, `description`, `version`, `url`): whoever installs it starts
it, and the page lists it and opens its `url`. Argus does not reflect, score or curate;
when to write or change a skill is the roles' decision.

**Sandbox.** On Linux with bubblewrap (`bwrap`), every backend call runs in a sandbox:
system directories read-only, the project directory writable (read-only for the Planner
and the Reviewer), and a private home into which only the CLI's login and settings are brought. Without
bubblewrap (macOS, Windows), Argus uses the CLI's own sandbox or permission settings where
it has them.

**Backends:** `OpenAI Codex CLI` · `Claude Code` · `GitHub Copilot CLI` · `Pi`

## WeChat community

Scan the QR code to join the Argus community. Click the image to open it at full
size. If the printed expiry date has passed, open an Issue and ask the
maintainers for the latest code.

<p align="center">
  <a href="docs/assets/argus-wechat-group-2.jpg?v=20260924">
    <img src="docs/assets/argus-wechat-group-2.jpg?v=20260924" width="360" alt="Argus WeChat Group 2 QR code">
  </a>
</p>

<p align="center"><strong>Community Group 1 is full. Please join Group 2.</strong></p>

## Install

You need Node.js **22.12+** from [nodejs.org](https://nodejs.org/en/download) and one
backend CLI, installed and signed in. Argus uses that CLI's login; there is no Argus
account. On Linux, also install bubblewrap (package `bubblewrap`) for the sandbox.

| Backend | `--backend` | Install | Sign in |
|---|---|---|---|
| OpenAI Codex CLI | `codex` (default) | `npm install -g @openai/codex@latest` | `codex login` |
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` | run `claude`, then `/login` |
| GitHub Copilot CLI | `copilot` | `npm install -g @github/copilot` | `copilot login` |
| Pi | `pi` | `npm install -g --ignore-scripts @earendil-works/pi-coding-agent` | run `pi`, then `/login` |

**From npm.** The package is not published yet; once it is, this is the whole install
(the name becomes `argus` when that name is available):

```bash
npm i -g @lbx154/argus
```

**From source**, until then:

```bash
git clone https://github.com/microsoft/ArgusAgent.git
cd ArgusAgent
npm ci
npm --prefix frontend/web ci
npm pack -w packages/core          # builds the core and its web page, writes lbx154-argus-<version>.tgz
npm i -g ./lbx154-argus-0.1.8.tgz
```

For the development preview, clone `https://github.com/lbx154/Argus.git` instead.

## Run

```bash
argus
```

It prints the address of the web page, `http://127.0.0.1:8799/?token=…` by default; open it in
a browser. There are no subcommands: everything else happens in the page.

| Option | |
|---|---|
| `--port N` | another port (default `8799`; `0` picks a free one) |
| `--backend codex\|claude\|copilot\|pi` | the first project's backend (default `codex`); later projects inherit the most recently created project |
| `--model M` | the first project's model, in the backend's own name; without it, the CLI's default |
| `--host H` | serve on another address, for example `0.0.0.0` |
| `--web DIR` | serve a web page built elsewhere instead of the bundled one |
| `ARGUS_WEB_TOKEN` | the access token; unset, one made on the first start and kept in the state root, so open pages stay signed in across restarts. The printed address carries it |
| `ARGUS_HOME` | the state directory (default `~/.argus`) |

In the page, create a project and tell the Manager what you want. A project works in its
own folder under `~/.argus/workspaces/`, or in an existing directory you give it. While
the team works you can keep talking to the Manager, follow the progress map (a card per
task, with every letter written about it), browse the project's files, open and download
results, and read the skill library under **Knowledge & skills**.

New projects inherit the most recently created project's backend, model and reasoning effort,
including backend defaults. Only the first project uses the startup options. Settings shows
which backend CLIs are installed, their reported versions and the actual bubblewrap launch
probe. The resources panel shows free space on the selected project's working-directory disk;
below 5 GiB it warns while work continues.

The work runs inside the `argus` process: closing the browser does not stop it. When the
process exits (including Ctrl-C), work stops. Starting Argus again with the same `ARGUS_HOME`
resumes projects that were running; projects you stopped in the page remain stopped. On a remote server,
run `argus` there and forward the port with `ssh -L 8799:127.0.0.1:8799 user@server`.
A walkthrough is in **[Getting started](docs/getting-started.md)**.

With `argus --host 0.0.0.0`, open a project's **Settings → Open on your phone**. Connect
both devices to the same network and scan the QR code. It uses a host interface address,
the listening port and the existing access token. If the machine has several interfaces,
choose the address reachable from the phone. The token grants access to your projects.

### Run at login

A user service keeps Argus independent of a terminal. These examples start it at login;
they do not restart an exited process. Replace the absolute Node and Argus paths with
`command -v node` and `command -v argus`, and use the same state root as your terminal.
Include the directory containing your backend CLI in `PATH`; service managers do not load
your shell profile. Stop a terminal instance before starting the service.

**Linux — systemd user service.** Save as `~/.config/systemd/user/argus.service`:

```ini
[Unit]
Description=Argus

[Service]
ExecStart=/absolute/path/to/node /absolute/path/to/argus
Environment=ARGUS_HOME=%h/.argus
Environment=PATH=/absolute/path/to/bin:/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now argus.service
journalctl --user -u argus.service   # includes the token-bearing cockpit address
systemctl --user stop argus.service
systemctl --user start argus.service # resumes work that was running
```

This service follows your user session. To keep it running after logout, your system must
allow user lingering (`loginctl enable-linger "$USER"`); the machine must remain awake.
See the [systemd user manager](https://www.freedesktop.org/software/systemd/man/latest/systemd.html)
and [service configuration](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html).

**macOS — launchd user agent.** Save as `~/Library/LaunchAgents/cn.argus.agent.plist`,
replacing `/Users/you` and the executable paths (launchd does not expand `~` or `$HOME`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>cn.argus.agent</string>
  <key>ProgramArguments</key><array>
    <string>/absolute/path/to/node</string>
    <string>/absolute/path/to/argus</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>ARGUS_HOME</key><string>/Users/you/.argus</string>
    <key>PATH</key><string>/absolute/path/to/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>/Users/you/Library/Logs/argus.log</string>
  <key>StandardErrorPath</key><string>/Users/you/Library/Logs/argus-error.log</string>
</dict></plist>
```

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/cn.argus.agent.plist
launchctl kickstart gui/$(id -u)/cn.argus.agent  # start again after a process exit
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/cn.argus.agent.plist
```

The agent runs while you are logged in. See Apple's
[launchd guide](https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html).

**Desktop.** The desktop app carries Argus and runs it with the machine's Node.js 22+, then
opens the cockpit it serves; see **[Desktop](docs/windows-desktop.md)** and the
[Releases](https://github.com/lbx154/Argus/releases) page.

## Upgrade and uninstall

Stop `argus` first; work under way is interrupted and resumes on the next start with the same state root.

```bash
npm i -g @lbx154/argus@latest      # from source: git pull, then the source steps above again
npm rm -g @lbx154/argus
```

Uninstalling leaves `~/.argus` in place: projects and their records, the roles' skills,
and under `workspaces/` the folders of projects that were not given a directory. Delete
it only if you want all of that gone.

## Projects started with the Python runtime

The earlier Python runtime (`argus/`) is still in this repository only so that projects
started with it can finish; after that it will be removed, and it is not for new
installations. Its state is in `~/.argus-skill`, separate from `~/.argus`. Keep running
those projects with the installation you started them with. Both installations provide a
command called `argus`; on a machine with both, check which one runs (`type -a argus`, or
`Get-Command argus -All` in PowerShell).

## Repository layout

- `packages/core/` — the npm package: the hand-offs between roles (`src/flow.ts`), backend
  calls and the sandbox (`src/agent/`), the web server (`src/serve/`), the role prompts
  (`prompts/`) and the bundled skills (`skills/`).
- `packages/contracts/`, `packages/runtime/` — shared types and helpers, bundled into the
  core when it is built.
- `frontend/web/` — the web page's source; the package carries a build of it.
- `desktop-tauri/` — the desktop shell.
- `argus/`, `tests/` — the Python runtime and its tests, kept until the projects started
  with it end.

## Further reading

- **[The TypeScript core](docs/typescript-core.md)** — how the core works, and what it
  deliberately leaves out.
- Written while running the Python runtime this core was simplified from:
  **[what goes wrong and what we did about it](docs/failure-modes-and-fixes.md)** — six
  real failure modes, and a fix we got wrong;
  **[system audit](docs/system-audit.md)** — six complaints checked against the code;
  **[exploration without local hill climbing](docs/exploration-without-local-hill-climbing.md)**
  — separating exploration from the claims a project makes.

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
