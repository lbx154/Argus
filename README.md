<h1 align="center">argus-skill</h1>

<p align="center"><strong>Supervised skill-driven coding agent.</strong></p>

<p align="center">
A merge of <a href="https://github.com/lbx154/skill-agent">skill-agent</a>'s
<em>horizontal</em> skill reuse and <a href="../ArgusBot">ArgusBot</a>'s
<em>vertical</em> reviewer-loop supervision.
</p>

```text
 █████╗ ██████╗  ██████╗ ██╗   ██╗███████╗      ███████╗██╗  ██╗██╗██╗     ██╗
██╔══██╗██╔══██╗██╔════╝ ██║   ██║██╔════╝      ██╔════╝██║ ██╔╝██║██║     ██║
███████║██████╔╝██║  ███╗██║   ██║███████╗█████╗███████╗█████╔╝ ██║██║     ██║
██╔══██║██╔══██╗██║   ██║██║   ██║╚════██║╚════╝╚════██║██╔═██╗ ██║██║     ██║
██║  ██║██║  ██║╚██████╔╝╚██████╔╝███████║      ███████║██║  ██╗██║███████╗███████╗
╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝  ╚═════╝ ╚══════╝      ╚══════╝╚═╝  ╚═╝╚═╝╚══════╝╚══════╝
```

<p align="center"><img src="docs/demo.svg" alt="argus-skill 7x24 daemon demo — planner creates high-impact work, engineer fixes it, Telegram reports progress" width="900"></p>

<p align="center"><sub>Replay on your own terminal: <code>asciinema play docs/demo.cast</code>. Rebuild with <code>python docs/build_demo.py</code>.</sub></p>

## What it is

argus-skill runs a coding task through this pipeline:

```
task → skill matcher → distill new skill if no high-fit match
     ↓
     supervised engineer round-loop
       round k: engineer turn → user-provided checks → reviewer
         done    → write skill back, return success
         continue → inject reviewer.next_action, next round
         blocked  → stop with reason
```

* **Horizontal layer** (from skill-agent): every task seeds a markdown
  *capability skill* in `skills/`. Future similar tasks reuse the same
  skill at near-zero cost. The matcher is fit-graded — only `high`-fit
  skills are applied, so the wrong skill cannot drag the engineer into a
  sibling sub-domain.

* **Vertical layer** (from ArgusBot): the engineer doesn't ship as a
  one-shot. A reviewer sub-agent reads the engineer's output plus the
  user's acceptance checks (`pytest -q`, `make test`, etc.) and emits a
  structured `{done, continue, blocked}` verdict. On `continue` the
  reviewer's `next_action` is fed back into the next engineer round.

* **Research-paper defaults** (adapted from ARIS workflow concepts):
  first-time initialization seeds built-in skills for research planning,
  full auto-research orchestration, benchmark execution, result
  analysis/figures, EMNLP-style paper drafting, submission assurance gates,
  paper-quality calibration against positive/negative examples, paper revision
  loops, and claims-evidence audits. These land in
  `~/.argus-skill/skills/` and are never overwritten if you edit them.

These two layers are orthogonal and multiplicative: the matcher cuts the
search-space cost; the reviewer-loop cuts the failure cost.

## Get started in 5 minutes

### 1. Run the no-key local demo

The repo ships an in-memory deterministic backend, so anyone can
smoke-test the cockpit, backlog, daemon, and event surfaces without an
API key:

```bash
git clone https://github.com/lbx154/argus-skill.git
cd argus-skill
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

# Deterministic, no network, safe to run anywhere.
ARGUS_SKILL_LIFE_BACKEND=memory argus-skill --no-daemon
```

In the REPL, type a task such as `ship me a base64 helper`; use
`/status`, `/backlog`, `/journal`, and `/exit` to inspect the state.

### 2. Start the real 7×24 agent

Use this on a server, dev box, or cloud VM with a working `codex`
binary. The daemon survives terminal logout and drains the current
project backlog in the background:

```bash
export ARGUS_SKILL_LIFE_BACKEND=codex
export ARGUS_SKILL_RUNNER_BIN="$(command -v codex)"
export ARGUS_SKILL_PER_MISSION_CAP_USD=30
export ARGUS_SKILL_DAILY_CAP_USD=180

argus-skill --init-identity
argus-skill --daemon --continuous \
  --objective "Keep this repo production-ready: keep tests green, docs accurate, and operator UX reliable."

argus-skill --status
argus-skill --watch   # live cockpit
argus-skill --follow  # pretty event tail
```

Stop it cleanly with `argus-skill --daemon-stop`.

### 3. Optional: control it from Telegram

Create a bot with BotFather, get your chat id, then start the daemon
with:

```bash
export ARGUS_SKILL_TELEGRAM_BOT_TOKEN="123:abc"
export ARGUS_SKILL_TELEGRAM_CHAT_ID="123456789"
argus-skill --daemon
```

Plain Telegram text is natural: if a task is running, it becomes live
operator guidance for the next engineer round; if idle, it becomes a
new mission with an immediate acknowledgement and progress cards. Use
`/status`, `/backlog`, `/nudge`, `/stop <id>`, and `/help` for explicit
control.

## Recommended use case: remote repo hardening

The strongest fit is leaving argus-skill on a repo overnight or during
work breaks:

1. L4 planner inspects tests, docs, runtime behavior, TODOs, and
   operator surfaces.
2. It queues only evidenced high-impact work (`impact_score >= 4`):
   correctness, reliability, integration, security, performance, or
   operator UX.
3. L1 engineer implements; L2 reviewer verifies; L3 critic rejects
   low-value polish and either asks for one more high-impact pass or
   hands control back to L4.
4. You watch from `--watch`, `--follow`, or Telegram, and can nudge it
   without interrupting an in-flight LLM call.

```text
Telegram: "修一下 --follow 看不出当前任务的问题，并加回归"
argus: 收到，我会把这当作一个新任务来做。
argus: 🧭 正在匹配可复用技能
argus: 🧾 实时动作 · 读 formatter 和 subprocess tests
argus: ✅ 任务已完成 · --follow now shows title + objective on start/complete
```

To skip the polish pass on a single item, use `/add --once <objective>`
or `argus-skill --notify "<guidance>"` to nudge the current work.

### One-shot CLI actions

These top-level flags run their action and exit instead of dropping
into the REPL:

* `--status` - print the current project daemon, backlog, and inbox
  summary.
* `--watch` - open the live read-only cockpit for the current project.
* `--follow` - tail the project event log with the pretty renderer.
* `--notify MSG` - append a nudge to the project inbox.
* `--init-identity` - seed the global identity card (and current
  project scaffold on first run).
* `--skill-stats` / `--skill-stats-json` - print the skill effectiveness
  report as text or JSON.
* `--skill-cleanse` / `--skill-compact` - run the skill maintenance
  helpers; add `--apply` to mutate disk instead of dry-run.
* `--daemon-runbook` - print the safe upgrade / restart checklist.

## Real usage (with codex / claude-code)

Drive a real CLI by setting the env vars and running the unified entry
point. The REPL handles the rest — auto-spawning the daemon,
distilling skills, supervising the engineer, and iterating on the
artefact until the critic's value gate says local polish is no longer
worth another round.

### Option A — `ARGUS_SKILL_LIFE_BACKEND=codex` (zero code)

The CLI ships a `CodexRunnerBackend` adapter that wraps ArgusBot's
battle-tested `codex_autoloop.codex_runner.CodexRunner`. Set the env
vars and run:

```bash
ARGUS_SKILL_LIFE_BACKEND=codex \
ARGUS_SKILL_RUNNER_BIN=$(which codex) \
argus-skill
```

Inside the REPL, type the task. The same supervisor that drives the
in-memory demo now talks to your real `codex` CLI.

Honoured env vars:

| Variable | Meaning | Default |
|----------|---------|---------|
| `ARGUS_SKILL_LIFE_BACKEND` | daemon backend: `memory` (stub) or `codex` (real CLI) | `codex` |
| `ARGUS_SKILL_RUNNER_BACKEND` | subprocess backend: `codex` / `claude` / `copilot` | `codex` |
| `ARGUS_SKILL_RUNNER_BIN` | path to the CLI binary used by the runner adapter | resolved on `$PATH` |
| `ARGUS_SKILL_RUNNER_EXTRA_ARGS` | shell-quoted args appended to every runner call | empty |
| `ARGUS_SKILL_TELEGRAM_BOT_TOKEN` | optional Telegram bot token for inbound control | unset |
| `ARGUS_SKILL_TELEGRAM_CHAT_ID` | Telegram chat id accepted by the poller | unset |
| `ARGUS_SKILL_TELEGRAM_USER_ID` | optional Telegram sender id filter | unset |

Requires the codex extra (`pip install 'argus-skill[codex]'`).

`memory` is the deterministic smoke backend; it can run single-shot
missions, but continuous mode requires the planning-capable `codex`
backend.

Continuous mode is validated up front: `--objective` must be paired
with `--continuous`, `--continuous` requires a non-empty objective, and
`memory` cannot enter planner mode.

### Telegram bridge (optional)

If `ARGUS_SKILL_TELEGRAM_BOT_TOKEN` and `ARGUS_SKILL_TELEGRAM_CHAT_ID`
are set, the daemon starts a long-polling Telegram command bridge.
`ARGUS_SKILL_TELEGRAM_USER_ID` is optional; when present, only that
Telegram sender is accepted.

Supported inbound commands:

* `/help` - show the command list.
* `/status` - summary of daemon, continuous mode, current work, backlog/history, inbox, and budget/cost.
* `/config [key=val ...]` - view/change session defaults.
* `/identity` - view the identity card.
* `/identity set <text>` - update the identity card with one message.
* `/project` - view the project card.
* `/project set <text>` - update the project card with one message.
* `/backend [codex|memory]` - show or change the backend.
* `/reset` - drop the codex session.
* `/skills [ls|promote <name>]` - list global skills or promote a project skill.
* `/backlog [all]` - list pending (or all) items.
* `/add <text> [--once] [--cycles=N] [--budget=$X]` - enqueue a mission.
* `/done <id>` / `/skip <id>` / `/rm <id>` - change item status.
* `/stop <id>` - disable iteration on an item; finalizes a pending item as done when applicable.
* `/start [objective]` - start continuous mode.
* `/continuous start|stop [objective]` - control continuous mode explicitly.
* `/journal [N]` - tail the recent journal.
* `/note <text>` - append a manual journal note.
* `/nudge <text>` - send live operator guidance.
* `/run [opts]` - drain the backlog.

Plain Telegram text is responsive: while a daemon mission is running it
is injected as a live nudge for the next engineer round / mission
prompt, with an immediate acknowledgement. When idle, plain text becomes
a new mission with a natural "received, I will work on it" reply. The
daemon streams pre-engineer progress too (skill matching, temporary
strategy distillation, file reads, tests), so Telegram does not look
silent while it prepares the run. Use `/add` explicitly whenever you
want to queue parallel follow-up work.

`/start` follows the same guardrails as the CLI: the objective must be
non-empty, and the `memory` backend cannot plan.

### Option B — custom backend (full control)

For non-codex / non-claude CLIs, import `SkillLoop` from your own
driver script and pass a `RunnerBackend` that wraps your CLI:

```python
from pathlib import Path
from argus_skill import SkillLoop, SkillLoopConfig
from argus_skill.core.ports import RunnerBackend
from argus_skill.core.models import RunnerOptions, RunnerResult

class CodexBackend:
    """Wrap subprocess `codex exec` here. Implement run_exec(...)."""
    def run_exec(self, *, prompt, options: RunnerOptions, run_label,
                 resume_thread_id=None) -> RunnerResult:
        ...  # call your CLI, parse stdout, return RunnerResult

backend = CodexBackend()
loop = SkillLoop(
    skills_dir=Path("skills"),
    scientist_runner=backend,
    engineer_runner=backend,
    reviewer_runner=backend,   # can be a cheaper model
    config=SkillLoopConfig(
        scientist_model="gpt-5.4",
        engineer_model="gpt-5.4-mini",
        reviewer_model="gpt-5.4-mini",
        max_rounds=3,
        check_commands=["pytest -q"],
    ),
)

outcome = loop.run("fix the failing tests in src/foo/")
print(outcome.status, outcome.round_count, outcome.skill_used)
```

The same `RunnerBackend` interface works for codex, claude-code, copilot
CLI, or anything else — only the wrapper changes.

## Run as a 7×24 lifetime agent

`argus-skill` (with no subcommand) drops into the **unified REPL**: a
single foreground process that owns the supervisor, the per-project
backlog, the journal, the layered skill cache, and the per-project
process state. Free text becomes a mission immediately; slash
commands manage the state. Top-level one-shot flags cover status,
cockpit, daemon, and skill-admin actions.

```bash
ARGUS_SKILL_LIFE_BACKEND=codex argus-skill
```

```text
argus › fix the failing tests in src/foo/
▶ mission start — fix the failing tests in src/foo/
🔧 round 1: main agent finished
🧑‍⚖️ round 1: review • done@0.92
■ mission complete  ·  status=success  ·  rounds=1  ·  cost=$0.0012

argus › /journal 5             # tail recent journal entries
argus › /backlog               # see what's queued (other terminals can /add)
argus › /skills ls             # global skill library
argus › /skills promote my-fix # move a project skill to global
argus › /nudge keep the null-path edge case in mind
argus › /exit                  # bye
```

The persistent state lives under `~/.argus-skill/`:

| Path | Scope |
| --- | --- |
| `identity.md`, `journal.jsonl` | global (cross-project) |
| `skills/` | global skill library, seeded with built-in research/paper skills |
| `skills/_archive/` | retired skills |
| `capabilities/model_api.json` | private model/image API grant (0600, outside repo) |
| `projects/<fingerprint>/project.md` | per-project card |
| `projects/<fingerprint>/memory.jsonl` | per-project memory journal |
| `projects/<fingerprint>/backlog.jsonl` | per-project backlog |
| `projects/<fingerprint>/skills/` | per-project skill cache |
| `projects/<fingerprint>/continuous.json` | per-project continuous-mode state |
| `projects/<fingerprint>/events.jsonl` | per-project event log / watch feed |
| `projects/<fingerprint>/inbox.jsonl` | per-project operator nudge queue |
| `projects/<fingerprint>/daemon.pid` / `daemon.status.json` / `repl.pid` | per-project process state |

Run `argus-skill --status` to inspect the current project backlog, the
project-local daemon state, and the shared global journal without
entering the REPL.

### Unified model / image API config

Text-model and `gpt-image-2` access is centralized in one private vault file:
`~/.argus-skill/capabilities/model_api.json`. The file is route-based, so
`engineer`, `reviewer`, `scientist`, `image`, and `image_review` can each use
different URLs, API keys, providers, wire APIs, and models. It is outside the
repository, written with mode `0600`, and is the only place tool subprocesses
load raw API keys from.

```bash
# One-time import from environment variables and/or Codex config.
export OPENAI_API_KEY="<your key>"
export OPENAI_BASE_URL="https://ai4m6.openai.azure.com/openai/v1/"
export ARGUS_SKILL_IMAGE_MODEL="gpt-image-2"
export ARGUS_SKILL_IMAGE_REVIEW_MODEL="gpt-5.4"
argus-skill --init-model-api
unset OPENAI_API_KEY

# Secret-free status check.
argus-skill --model-api-status
```

If you keep provider settings in a project-local `.codex/config.toml`, point the
importer at it once:

```bash
ARGUS_SKILL_CODEX_CONFIG=.codex/config.toml argus-skill --init-model-api
```

For split endpoints, set route-specific variables before import, e.g.
`ARGUS_SKILL_IMAGE_BASE_URL`, `ARGUS_SKILL_IMAGE_API_KEY`,
`ARGUS_SKILL_ENGINEER_BASE_URL`, and `ARGUS_SKILL_ENGINEER_API_KEY`. See
`docs/API_CONFIG.md`.

### Background daemon (real 7×24)

The REPL is the cockpit; the daemon is the engine room. Running
`argus-skill --daemon` spawns a detached background worker that drains
your backlog forever — even after you close the terminal, log out, or
the REPL exits.

```bash
argus-skill --daemon          # detach a worker, returns immediately
argus-skill --status          # one-shot status (daemon/backlog/continuous)
argus-skill --daemon-stop     # graceful SIGTERM
argus-skill --daemon-fg       # run in the foreground (for systemd / debugging)
```

The daemon and the REPL coexist safely:

* They use **separate PID locks** (`<project-root>/repl.pid` vs
  `<project-root>/daemon.pid`) — neither blocks the other.
* They share the **same atomic state machine**: every mission goes
  through `Backlog.claim_next()` (an atomic `pending → running` CAS on
  the JSONL file). Two workers cannot pick the same mission, even
  under contention.
* They share the **same continuous-state file** (`continuous.json`):
  `argus-skill --status` reports whether continuous mode is enabled,
  which objective is active, and any recorded `done_reason` / `done_at`.
* Continuous mode optimizes for sustained high-value work: L3 rejects
  low-impact polish loops, then L4 searches wider project value horizons
  and queues the next evidenced, high-impact mission.
* If a worker dies hard, the next process startup reaps any stuck
  `running` items and marks them `failed` — re-execution is impossible
  because terminal states are sealed (`IllegalStateTransition`).

When the REPL launches with a live daemon, the banner says so:

```text
mode    → life  ⚡ daemon: pid 12345 · up 4h 12m
backend → codex   (/backend memory|codex)
```

### Will the daemon survive when I close the terminal / disconnect SSH / sleep?

Yes — by design, with one OS-level caveat to know about.

The detach mechanism: `argus-skill --daemon` (or the auto-spawn when
the REPL launches) does **POSIX double-fork + `setsid` + `chdir("/")`
+ stdio → log file + SIGHUP ignored**. After the spawn:

* `PPID = 1` (init / systemd) — the daemon is fully reparented.
* It lives in **its own session**; closing the terminal or
  disconnecting SSH cannot deliver SIGHUP to it.
* `argus-skill --status` reports `survival : linger=on` when your
  Linux user has logout-survival enabled.

What can still kill it:

1. **`systemd-logind KillUserProcesses=yes`** (default on some
   distros) — kills *all* processes owned by your user when you log
   out, regardless of session. Fix once per user:

   ```bash
   loginctl enable-linger $USER
   ```

   `argus-skill --status` warns when linger is off.

2. **The machine itself sleeps** — laptop closes lid, OS suspends
   CPU, the daemon (and codex) pause until wake. For real 7×24 run
   on a server, a desktop with sleep disabled, or a cloud VM.

3. **`pkill python` or similar broad kills** — operator error, no
   software can defend against this.

For the strictest guarantee, run under systemd as a user service so
restart-on-crash + boot-on-reboot are handled by the OS — see
[Running under systemd](#running-under-systemd) below.

A typical workflow: leave the daemon running on a small server, drop
into the REPL from your laptop to inspect `/status`, `/backlog`, or
`/journal`, add or nudge work, then walk away. The daemon keeps
draining.

### Running under systemd

```ini
# /etc/systemd/system/argus-skill.service
[Unit]
Description=argus-skill 7×24 lifetime agent
After=network.target

[Service]
Type=simple
User=ops
Environment=ARGUS_SKILL_LIFE_BACKEND=codex
Environment=ARGUS_SKILL_RUNNER_BIN=/usr/local/bin/codex
Environment=ARGUS_SKILL_PER_MISSION_CAP_USD=1.0
Environment=ARGUS_SKILL_DAILY_CAP_USD=10.0
ExecStart=/usr/local/bin/argus-skill --daemon-fg
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

`--daemon-fg` keeps the worker in the foreground and lets systemd own
the process lifecycle (restart, logging, stop). Use `--daemon` only for
ad-hoc detach in interactive sessions.

If you are about to upgrade the code while the daemon is the thing
keeping your current session alive, run `argus-skill --daemon-runbook`
from a separate shell first. It prints the safe sequence: checkpoint,
stop from the outside, update, relaunch, verify.

## Research paper generation (auto-paper pipeline)

argus-skill ships **34 built-in research skills** that cover the full
lifecycle of an academic paper — from ideation to camera-ready PDF.
All skills are self-contained in the repo; no external dependencies.

### Quick start: generate a paper

```bash
git clone https://github.com/lbx154/argus-skill.git
cd argus-skill
python -m venv .venv && . .venv/bin/activate
pip install -e ".[codex]"

# Create a new research project (interactive wizard)
python -m argus_skill.tools.new_auto_research_project \
  --parent ~/research \
  --title "My EMNLP Paper" \
  --no-start

# Or start immediately with the daemon
python -m argus_skill.tools.new_auto_research_project \
  --parent ~/research \
  --title "My EMNLP Paper" \
  --start-daemon
```

The project launcher creates a full scaffold: `AGENTS.md`, `research/`,
`code/`, `experiments/`, exported builtin skills, and a pipeline state
machine starting at the `literature` stage.

### Supported domains

| Domain | Training? | Example Topics |
|--------|-----------|----------------|
| Agent / LLM | Free or trained | Multi-agent, tool-use, RAG, planning, reward models |
| Computer Vision | Free or trained | Detection, segmentation, ViT, 3D vision |
| Multimodal / VLM | Free or trained | LLaVA-style, VQA, video-language |
| AI Infrastructure | Free or trained | Serving, kernels, distributed training, MLSys |
| NLP | Free or trained | Pretraining, fine-tuning, summarization, parsing |
| RL / Alignment | Free or trained | RLHF, DPO, reward modeling, constitutional AI |

**Domain and methodology are orthogonal** — any domain can use
training-free (prompting/API) or training-based (gradient) approaches.
The `research-domain-router` skill auto-selects the right pipeline.

### Target venues

EMNLP, ACL, NeurIPS, ICML, ICLR, CVPR, ICCV, ECCV, MLSys, OSDI, ATC —
venue-specific formatting rules are built into the skills.

### The 12-stage pipeline

```
ideation → literature → hypothesis → experiment-plan → implementation
    → benchmark → results → analysis → drafting → review-loop
    → revision → submission-preflight
```

Each stage is governed by a builtin skill. The planner (L4) advances
the pipeline automatically; you can also run stages manually.

### Built-in skill inventory (76 skills)

**Orchestration & routing:**
- `auto-research-pipeline` — end-to-end orchestration
- `research-domain-router` — auto-detect domain × methodology
- `emnlp-paper-skill-router` — stage-aware skill dispatch

**Ideation & literature:**
- `research-ideation` — 10-framework structured brainstorming
- `novelty-check` — verify idea against recent literature
- `semantic-scholar-search` — published venue paper search
- `paper-exemplar-pdf-learning` — learn from positive/negative examples

**Experiment design & execution:**
- `research-brief-to-experiment-plan` — idea → runnable plan
- `agent-research-benchmark-runner` — training-free evaluation
- `domains/training/*` — GPU training and fine-tuning packs (DeepSpeed/FSDP/LoRA/RLHF)
- `domains/cv-multimodal/*` — CV/VLM packs (CLIP, LLaVA, SAM, BLIP-2, VQA/MMMU-style work)
- `domains/inference-serving/*`, `domains/infrastructure/*`, `domains/optimization/*` — systems benchmarking, serving, and efficiency packs
- `domains/research-ops/run-experiment`, `domains/research-ops/monitor-experiment` — experiment execution and monitoring
- `ablation-planner` — systematic ablation study design
- `experiment-audit` — integrity check (fake GT, normalization fraud)

**Results & claims:**
- `result-to-claim` — experiment results → supported claims
- `claims-evidence-audit` — evidence sufficiency check
- `research-results-analysis-and-figures` — stats + visualization

**Writing & figures:**
- `emnlp-paper-drafting` — section-by-section LaTeX drafting
- `emnlp-paper-writing-playbook` — 800-line operational playbook
- `paper-illustration-image2` — gpt-image-2 multi-stage figures
- `emnlp-academic-language-review` — language quality gate

**Review & submission:**
- `paper-review-revision-loop` — iterative review/fix cycle
- `academic-paper-peer-review-benchmark` — calibrated review
- `emnlp-format-preflight` — LaTeX/format compliance check
- `domains/research-ops/citation-audit` — bibliography and citation-context audit
- `domains/research-ops/paper-compile`, `domains/research-ops/paper-figure` — paper build and figure support
- `research-submission-assurance-gate` — final submission gate

**Agent roles (L1-L4):**
- `argus-planner-role`, `argus-critic-role`, `argus-reviewer-role`,
  `argus-engineer-role`, `argus-scientist-role`

**Templates:**
- `agent-md-new-project-template`, `agent-md-existing-project-optimization-template`
- `reviewer-engineer-handoff`

### Using with the daemon (7×24 auto-paper)

```bash
cd ~/research/my-emnlp-paper

export ARGUS_SKILL_LIFE_BACKEND=codex
export ARGUS_SKILL_RUNNER_BIN=$(which codex)

# Start daemon with a research objective
argus-skill --daemon --continuous \
  --objective "Complete the EMNLP paper: run all experiments, \
  produce figures, write all sections, pass format preflight."

# Monitor progress
argus-skill --watch    # live cockpit
argus-skill --follow   # event stream
argus-skill --status   # one-shot summary
```

The planner will advance through pipeline stages automatically,
queuing work for each stage and verifying completion before moving on.

### Manual stage execution

You can also run individual stages from the REPL:

```text
argus › run the novelty check for our method
argus › design ablation studies for the main claim
argus › draft the introduction section
argus › run format preflight on paper/main.tex
```

### Extending with custom skills

Drop a markdown file in `~/.argus-skill/skills/` with YAML frontmatter:

```markdown
---
name: my-custom-evaluation
description: "Run my custom benchmark suite"
category: experiment-execution
version: "1.0"
---

# My Custom Evaluation

[Your skill instructions here...]
```

The matcher will pick it up automatically for future tasks that match.

## Architecture at a glance

```
argus_skill/
├── core/
│   ├── models.py        # CheckResult, ReviewDecision, RunnerOptions, RunnerResult, LoopOutcome
│   ├── ports.py         # RunnerBackend, SkillSource, ControlChannel, EventSink protocols
│   └── project.py       # cwd → project fingerprinting
├── scientist/
│   ├── prompts.py       # vendored from skill-agent (Coverage check + When NOT to use + fit-graded matcher)
│   └── distiller.py     # calls runner with Prompts.distill(...)
├── skills/
│   ├── layered.py
│   ├── lifecycle.py
│   ├── quality.py
│   └── store.py         # vendored from skill-agent (markdown cache + fit-graded matcher)
├── engineer/
│   ├── reviewer.py      # vendored from ArgusBot (refactored to take RunnerBackend protocol)
│   ├── reviewer_schema.json
│   ├── checks.py        # vendored from ArgusBot
│   └── runner.py        # SupervisedEngineer: round-loop control flow (NEW)
├── adapters/
│   ├── memory_backend.py  # deterministic stub for tests / smoke runs (NEW)
│   ├── codex_backend.py   # CodexRunnerBackend — wraps ArgusBot's CodexRunner (NEW)
│   └── stream_progress.py # shared live-output plumbing for runner events
├── critic/
│   └── critic.py         # critic + planner logic for iteration / continuous mode
├── daemon/
│   ├── token_lock.py    # vendored verbatim from ArgusBot (single-process token guard)
│   └── life_worker.py   # 7×24 background worker around LifeSupervisor (NEW)
├── apps/
│   ├── cli.py           # `argus-skill` entry point, one-shot actions, REPL fallback
│   ├── _life_repl.py    # the unified REPL surface
│   ├── _life_actions.py # shared non-interactive backlog / config / status helpers
│   ├── _inbox.py       # shared inbox queue / drain / event formatting helpers
│   ├── _skill_stats.py
│   ├── _skill_cleanse.py
│   ├── _watch.py
│   ├── _init_identity.py
│   ├── _input_helpers.py
│   └── _target_paths.py # shared life-dir / project-root resolution helpers
├── cli/
│   ├── branding.py
│   ├── event_format.py
│   ├── render.py
│   └── theme.py
├── life/
│   ├── event_log.py
│   ├── memory.py
│   ├── status.py       # shared backlog / continuous-state selectors
│   ├── telegram_bot.py  # optional Telegram inbound command bridge
│   ├── notify.py
│   ├── router.py
│   └── supervisor.py
└── loop.py              # SkillLoop — the matcher × distiller × supervised-engineer GLUE
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a per-file
provenance map ("which file came from which upstream").

**Related docs** (added 2026-05-10 after the v12 retrospective):

- [docs/PRICING.md](docs/PRICING.md) — canonical OpenAI model pricing & cache-discount math. Read before computing any cost.
- [docs/EXPERIMENT_PROTOCOL.md](docs/EXPERIMENT_PROTOCOL.md) — mandatory pre-run / post-run checklist for any experiment. Designed so a months-later forensic on tonight's data won't require archaeology.
- [docs/RETROSPECTIVE-v12-vs-current.md](docs/RETROSPECTIVE-v12-vs-current.md) — why the 2026-05-06 v12 fullbench hit 0.596 reward at $0.14/trial (≈ 42% of codex-bare large baseline) while the current v4-pri-2 ablation is misleadingly cheap because it disabled the components that did the work.
- [benchmarks/reports/2026-05-10-tb2-baseline-vs-v12.md](benchmarks/reports/2026-05-10-tb2-baseline-vs-v12.md) — full head-to-head comparison of v12 against bare-large (`gpt-5.4`) and bare-mini (`gpt-5.4-mini`) baselines on TB v2's same 89-task dataset commit. Includes pairwise win/loss diff and corrected cost reconstruction.

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

The default suite includes parser contracts, docs contracts, installed-
wheel smoke checks, and in-memory backend coverage. Run `pytest -q`
for the full gate. The end-to-end smoke test (`tests/test_loop_smoke.py`)
exercises:

* matcher miss → distill → 2 rounds (continue → done) → skill writeback
* second-run cache hit (matcher returns high-fit, no redistill)
* reviewer says blocked → loop short-circuits in 1 round
* every round says continue → loop hits `max_rounds` cleanly
* `--no-distill-on-miss` falls back to running engineer skill-less

## Status

v0.1. End-to-end working with two backends:

* `ARGUS_SKILL_LIFE_BACKEND=memory` — deterministic in-process stub used
  by the test suite, demos, and CI. No API key required.
* `ARGUS_SKILL_LIFE_BACKEND=codex` (default) — drives a real codex CLI
  through `CodexRunnerBackend`. Requires a working `codex` binary on
  `$PATH`.

The unified REPL is the primary entry point. Top-level one-shot flags
cover status, cockpit, daemon, and skill-admin actions. The detached
daemon and Telegram poller share the same split memory state: global
identity/journal live at the shared root, while backlog, project
memory, events, and process locks live under `projects/<fingerprint>/`.
Cross-process safety is provided by a per-project singleton lock
(`projects/<fingerprint>/repl.pid`) and a state-machine seal that makes
terminal backlog items unrunnable.

Shared helper modules now used by `apps/cli.py`, `apps/_life_repl.py`,
`apps/_watch.py`, and `life/telegram_bot.py`:

* `apps/_inbox.py` — inbox queue, drain, count, and event formatting.
* `apps/_life_actions.py` — backlog mutations, config helpers, status
  change renderers, and shared `/run` plumbing.
* `apps/_target_paths.py` — global/project life-root resolution.
* `life/status.py` — backlog-status selection and continuous-state
  description helpers.

## License

MIT — see [LICENSE](LICENSE).

## Provenance

* [skill-agent](https://github.com/lbx154/skill-agent) (Phase A as of
  2026-05-03): the `skill_store.py`, `prompts.py`, and the
  Coverage / When NOT to use / fit-graded matcher prompt engineering.
* [ArgusBot](../ArgusBot): the `reviewer.py`, `reviewer_schema.json`,
  `checks.py`, and the LoopEngine round-loop control flow.
* New code in argus-skill: `core/models.py`, `core/ports.py`,
  `engineer/runner.py` (SupervisedEngineer), `loop.py` (SkillLoop), the
  in-memory backend, the CLI, the tests.
