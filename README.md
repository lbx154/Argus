<h1 align="center">Argus</h1>

<p align="center"><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></p>

<p align="center"><strong>One goal in.<br>An autonomous AI research team wakes up.</strong></p>
<p align="center"><em>Configure the machine policy once. Manager, Planner, Engineer, and Reviewer take it from there.</em></p>

<p align="center">
  <img src="technical_report/figures/argus_architecture.png" alt="Argus architecture schematic: an operator objective enters a persistent harness where Manager, Planner, Engineer, and Reviewer make every research judgment" width="100%">
</p>

---

## What Argus is

Argus is a **7×24 harness for long-horizon autonomous research**, not a
polish-your-paper assistant and not a fixed prompt chain. You give it an
**objective** and a **special prompt** describing this machine's operating
rules (GPU allocation, working paths, scheduling constraints), and four
persistent roles — Manager, Planner, Engineer, and Reviewer — take it from
there: proposing work, executing it on real hardware, and deciding whether it
is actually done. Argus is a reliable harness and a live workbench for this
kind of work; it is not a guaranteed path to state-of-the-art results or a
guaranteed paper acceptance.

The live, day-to-day product is a **benchmark-reproduction agent** across
metric verticals such as nanochat, nanoGPT speedrun, and KernelBench. A
separate optional mode (the `research` vertical) drives an idea-to-submission
academic paper pipeline; it is not the default identity of the project.

## Why long-horizon autonomy fails without a harness

Give a capable model a single hard objective and enough time, and it does not
fail because it lacks intelligence — it fails because the *execution
substrate* degrades. Sessions get resumed dozens or hundreds of times and lose
working memory to lossy compaction. Idle rounds burn budget with no forward
motion. A long-running background experiment quietly blocks the main thread of
work. A reviewer and an engineer drift out of sync on what "done" means.
None of this is a reasoning problem, so patching it with better prompts does
not fix it. It has to be fixed structurally, in the harness, without the
harness pretending it knows better than the agent about the actual research.

## Four roles, one dumb pipe

Argus keeps a strict separation: **research judgment belongs to the agent**
(is this idea novel enough, is this baseline strong enough, is the experiment
finished, should we submit); **domain-agnostic plumbing belongs to the
harness** (budget and rate limits, disk persistence and memory, scheduling and
daemon lifecycle, structured I/O, anti-cheat guardrails). The harness is a
thick, dumb pipe — it feeds tasks to the agent, stores artifacts, and paces
work against budget. It never uses keyword or regex heuristics to second-guess
whether something is a paper task, whether an objective should run forever, or
whether an idea is good enough. Every temptation to let the harness quietly
override an agent's judgment has been removed and replaced with either an
explicit structured signal or a decision handed back to the agent.

The four roles:

- **Manager** is the single entry point for the operator. Free-form text is
  routed to chat or task by a model call, not a keyword match. It is also the
  sole authority for pipeline-stage transitions — the other three roles can
  only recommend a stage change.
- **Planner (L4)** queues the next unit of work once the backlog is empty in
  continuous mode, and redirects a premature "done" verdict into a
  certification task when full-pipeline completion has not yet been proven.
- **Engineer (L1)** executes a single round of real work: literature search,
  code, experiments, or manuscript writing, sharing a working directory with
  the Reviewer.
- **Reviewer (L2)** is the **sole authority on completion**. Each round it
  checks the Engineer's output against a stage checklist and returns `done`,
  `continue` (with a concrete next step), or `blocked`. There is no separate
  hardcoded completion gate — the Reviewer's structured verdict is the only
  source of truth.

A retired L3 "critic" round-polishing layer no longer exists; all acceptance
runs through the Reviewer. An optional Curator role handles skill-pool
maintenance in team/subagent modes, but does not participate in the default
single-mission pipeline.

## Reliability mechanisms

The biggest risk in long-horizon runs is execution drift, not weak reasoning,
so Argus ships several domain-agnostic guards that never make a scientific
judgment call themselves:

- **Curated working-memory checkpoint and session-roll.** A single mission's
  session gets resumed repeatedly and, left unchecked, accumulates lossy
  compaction that erases working memory. The Reviewer audits and rewrites a
  small, hard-capped checkpoint (goal, done items, tried-and-failed attempts,
  open blocker, next step) every round. The engineer session rolls over to a
  fresh session, reseeded from that checkpoint, after **3 consecutive rounds**
  or once the prior round's input reaches **1.5M tokens** — both configurable,
  either can be disabled — instead of letting a single thread compact without
  bound.
- **Structured decision-progress classification.** The Reviewer labels each
  round's `progress_class` (`decision`, `evidence`, `setup_only`,
  `artifact_sync_only`, or `none`). Two consecutive rounds classified as
  nondecision progress trip a stall counter, paired with a safe,
  round-boundary **1,800-second** budget measured from the last
  decision/evidence increment — this never interrupts an in-flight model call
  and never lets a mission spin indefinitely.
- **Dynamic review cadence.** An Engineer that has landed a real increment and
  has an unambiguous next step can request one deferred Reviewer round; it can
  only defer once in a row, and `done` can still only ever come from the
  Reviewer.
- **Background-subagent cadence wait.** When the Engineer starts a
  self-monitored long-running job (for example, GPU training), it can wait on
  that job's own monitoring cadence instead of polling a healthy run every
  round.
- **Live credential guard.** Newly written artifacts are scrubbed for
  credentials, domain-agnostically, before they reach the event log or the
  Reviewer's prompt.

## Auditable research programs

Argus's benchmark-reproduction verticals (KernelBench, nanoGPT speedrun,
nanochat, and similar) are designed to be run against real public benchmarks
with an auditable trail: real benchmark data, no reward hacking against a
known evaluation input distribution, and a persisted record of every round's
evidence, verdict, and skill update. Structured stage checklists — not
keyword-matched validators — are what the Reviewer checks against, and the
Reviewer is the only authority that can certify a research program complete.

Public status is intentionally modest: the KernelBench, nanoGPT speedrun, and
nanochat reproduction programs are **ongoing** work, not finished claims,
unless a reproducible in-repo evidence artifact proves otherwise for a given
run. Argus does not publicly claim to have produced an accepted paper through
its autonomous pipeline. Any external reference numbers that may appear in
project documentation (for example, published SOL/timing/loss baselines from
other work) are external or reference baselines used for comparison, not
Argus's own achieved results, and are labeled as such wherever they appear.

## Quick start

```bash
git clone https://github.com/lbx154/argus-skill.git
cd argus-skill
python -m venv .venv && . .venv/bin/activate
pip install -e .
```

Run the interactive setup wizard once, to configure author identity,
per-role API access, GPU allocation, and optional GPU keep-alive:

```bash
argus-skill --setup
```

Starting the daemon requires at least one trusted special prompt describing
this machine's operating rules:

```bash
mkdir -p ~/.argus-skill/special_prompts
printf 'Operational house rules for this box.\n' > ~/.argus-skill/special_prompts/10-house-rules.md
chmod 0644 ~/.argus-skill/special_prompts/10-house-rules.md
```

Then start a project from within its working directory:

```bash
mkdir -p ~/research/world-models && cd ~/research/world-models
argus-skill --daemon --continuous \
  --objective "World Model for Agent Action Selection"
```

For everyday interactive use, run `argus` to enter the Ink TUI cockpit; the
Manager will classify the first real task and decide whether it runs as a
standing (continuous) or bounded (one-shot) objective. Monitor with
`argus-skill --status`, `--watch`, or `--follow`.

## Supported backends

Argus drives one of three supported agent CLIs, selected with
`ARGUS_SKILL_RUNNER_BACKEND`. Python **>= 3.11** is required.

| Backend | Value | Install | Auth |
|---|---|---|---|
| GitHub Copilot CLI | `copilot` | `npm install -g @github/copilot` (Node.js >= 22) | First run of `copilot` does an interactive device authorization against your GitHub Copilot subscription — no separate API key or Azure/OpenAI vault needed |
| OpenAI Codex CLI | `codex` (the default when the variable is unset) | `npm install -g @openai/codex` | Verify with `codex --version`; API key configuration is in `docs/API_CONFIG.md` |
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` | First run of `claude` does an interactive login |

```bash
export ARGUS_SKILL_RUNNER_BACKEND=copilot
copilot --version
```

The backend and model do not need to be decided up front — from within the
cockpit you can say "switch to the copilot backend" and it takes effect
without editing a config file.

## Technical report

A full technical report on the architecture and evidence behind Argus will be
published at
[`technical_report/argus-technical-report.pdf`](technical_report/argus-technical-report.pdf).
That file does not exist yet in this repository; the link is a stable
placeholder that will resolve once the report is added.

## Limitations and status

Argus is under active development. It is a harness that gives an agent real
judgment, real tool access, and real persistence over long-horizon research
work — it is not a system that guarantees state-of-the-art results, a
guaranteed paper acceptance, or a finished, audited research program on every
vertical today. Treat any specific performance numbers you encounter in this
repository's documentation as either an explicitly labeled external/reference
baseline, or as a reproducible, in-repo evidence artifact for a specific run —
never as a general claim about Argus's guaranteed capability.

## License and provenance

Argus is MIT-licensed, as declared in `pyproject.toml`'s package metadata
(`license = { text = "MIT" }`). This repository does not currently check in a
root `LICENSE` file, so this section does not link one.

- [skill-agent](https://github.com/lbx154/skill-agent): skill matching and
  distillation.
- [ArgusBot](https://github.com/waltstephen/ArgusBot) (MIT): the reviewer loop
  and CLI runner. Its `codex_autoloop` module is vendored into
  `argus_skill/agent_cli/`, including the upstream
  [LICENSE](argus_skill/agent_cli/LICENSE) and `_VENDORED.md` provenance note.
- New code in this repository: the auto-research pipeline, stage checklists,
  built-in skills, and the image-2 figure integration.
