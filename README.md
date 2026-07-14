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
polish-your-paper assistant or a fixed prompt chain. You give it an
**objective** and a **special prompt** describing this machine's operating
rules (GPU allocation, working paths, scheduling constraints), and four
persistent roles — Manager, Planner, Engineer, and Reviewer — take it from
there: proposing work, executing it on real hardware, and deciding whether it
is actually done. It is a reliable harness and a live workbench, not a
guaranteed path to state-of-the-art results or paper acceptance.

The day-to-day product is a **benchmark-reproduction agent** across metric
verticals such as nanochat, nanoGPT speedrun, and KernelBench; a separate
optional `research` vertical drives an idea-to-submission paper pipeline, not
the project's default identity.

## Results

Argus's public results, published live on
[argusbot.cn/results.html](https://argusbot.cn/results.html) and
[/research.html](https://argusbot.cn/research.html), are compared **against
human SOTA, human-authored public records, or paper-reported bests first** —
never against another agent as the headline. Machine-readable evidence for
every row below: [`technical_report/evidence/website_results.json`](technical_report/evidence/website_results.json)
and [`technical_report/evidence/paper_inventory.json`](technical_report/evidence/paper_inventory.json).

| Arena | Protocol | Result | Human comparison |
|---|---|---|---|
| NVIDIA SOL-ExecBench | B200 · 101 kernels | Global #6 · 2× #1 · 7 top-3 | Two head-to-head wins over Recursive |
| nanochat · B200 | 5 min · 1×B200 · 426 attempts | 0.9636 BPB | Human SOTA: 0.9646 |
| nanochat · H100 | 5 min · 1×H100 · 37 mechanisms | 0.9855 BPB | Human SOTA: 0.9879 |
| nanoGPT speedrun | 8×H100 · N=10 | 79.77 seconds | Same-device human #83: 80.18s |
| AARRI-Bench | 82 research-intern tasks | 63/82 · 76.8% | Paper-reported best: 68.3% |
| Arbor · RUC NLPIR | Math-Reasoning Data | 28.0 gap | Arbor 20.83 · Claude Code 8.33 · Codex 6.25 |

Plus a **41-paper research collection** spanning six programs — Cognitive Bias
in LLMs (9), Multimodal & Vision-Language Models (16), LLM Agent Methods (5),
Efficiency/Compression/Decoding (7), World Models (2), and State Trace &
Auditability (2) — 35 manuscripts and 6 drafts, duplicates removed. The
collection is judged only against human-authored literature and human-SOTA or
strong-human baselines, never against another agent's paper count or quality.

Read every number with its qualifier, not in isolation: 79.77 s is **N=10,
verifier-certified** (`SCORE valid=true n=10 ... seal=ok`, corroborated
on-disk in `nanogpt-speedrun-h100`); 0.9636 BPB is the published website
result and matches the local one-seed, frozen-scorer floor of 0.963634 in
`nanochat-mission-b200`; the 426/37 attempt/mechanism counts are the website's
own published snapshot; and 41 is a de-duplicated paper-run inventory, not 41
acceptances — Argus makes **no paper-acceptance claim**. Four of the six arena
rows (SOL-ExecBench, nanochat H100, AARRI-Bench, Arbor) currently carry
website-snapshot evidence only, with no local reproduction artifact yet; that
is stated here plainly rather than hidden. The Arbor row is reported as
published on the site and is not elevated into an agent-vs-agent headline.

## Why long-horizon autonomy fails without a harness

Give a capable model a single hard objective and enough time, and it fails not
from a lack of intelligence but because the *execution substrate* degrades:
sessions get resumed until lossy compaction erases working memory, idle rounds
burn budget with no forward motion, a background experiment silently blocks
the main thread, and reviewer/engineer drift out of sync on what "done"
means. None of this is a reasoning problem, so it has to be fixed
structurally, in the harness — without the harness pretending it knows better
than the agent about the actual research.

## Four roles, one dumb pipe

Argus keeps a strict separation: **research judgment belongs to the agent**
(is this idea novel enough, is this baseline strong enough, is the experiment
finished, should we submit); **domain-agnostic plumbing belongs to the
harness** (budget and rate limits, disk persistence and memory, scheduling and
daemon lifecycle, structured I/O, anti-cheat guardrails). The harness is a
thick, dumb pipe — it feeds tasks to the agent, stores artifacts, and paces
work against budget, never using keyword or regex heuristics to second-guess
whether something is a paper task, whether an objective should run forever, or
whether an idea is good enough. Every temptation to let the harness quietly
override an agent's judgment has been replaced with either an explicit
structured signal or a decision handed back to the agent.

The four roles:

- **Manager** is the single entry point for the operator. Free-form text is
  routed to chat or task by a model call, not a keyword match, and it is the
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
  `continue` (with a concrete next step), or `blocked` — there is no separate
  hardcoded completion gate.

A retired L3 "critic" round-polishing layer no longer exists; all acceptance
runs through the Reviewer. An optional Curator role handles skill-pool
maintenance in team/subagent modes but does not participate in the default
single-mission pipeline.

## Reliability mechanisms

The biggest risk in long-horizon runs is execution drift, not weak reasoning,
so Argus ships several domain-agnostic guards that never make a scientific
judgment call themselves:

- **Curated working-memory checkpoint and session-roll.** A mission's session
  gets resumed repeatedly and, left unchecked, accumulates lossy compaction
  that erases working memory. The Reviewer audits and rewrites a small,
  hard-capped checkpoint (goal, done items, tried-and-failed attempts, open
  blocker, next step) every round. The engineer session rolls over to a fresh
  session, reseeded from that checkpoint, after **3 consecutive rounds** or
  once the prior round's input reaches **1.5M tokens** — both configurable,
  either can be disabled — instead of letting a single thread compact without
  bound.
- **Structured decision-progress classification.** The Reviewer labels each
  round's `progress_class` (`decision`, `evidence`, `setup_only`,
  `artifact_sync_only`, or `none`). Two consecutive nondecision rounds trip a
  stall counter, paired with a safe, round-boundary **1,800-second** budget
  measured from the last decision/evidence increment — this never interrupts
  an in-flight model call and never lets a mission spin indefinitely.
- **Dynamic review cadence.** An Engineer that has landed a real increment and
  has an unambiguous next step can request one deferred Reviewer round; it can
  only defer once in a row, and `done` can still only ever come from the
  Reviewer.
- **Background-subagent cadence wait.** An Engineer running a self-monitored
  long job (for example, GPU training) can wait on that job's own monitoring
  cadence instead of polling a healthy run every round.
- **Live credential guard.** Newly written artifacts are scrubbed for
  credentials, domain-agnostically, before they reach the event log or the
  Reviewer's prompt.

## Auditable research programs

Argus's benchmark-reproduction verticals (KernelBench, nanoGPT speedrun,
nanochat, and similar) run against real public benchmarks with an auditable
trail: real benchmark data, no reward hacking against a known evaluation input
distribution, and a persisted record of every round's evidence, verdict, and
skill update. Structured stage checklists — not keyword-matched validators —
are what the Reviewer checks against, and the Reviewer is the only authority
that can certify a research program complete. Beyond the evidence-linked
Results above, any external reference numbers in project documentation (for
example, published SOL/timing/loss baselines from other work) are external
comparison baselines, not Argus's own achieved results, and are labeled as
such wherever they appear.

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

A full technical report on the architecture, reliability mechanisms, and
evidence methodology behind Argus is available at
[`technical_report/argus-technical-report.pdf`](technical_report/argus-technical-report.pdf)
(Technical Report 0.1). The LaTeX source lives under `technical_report/` and
rebuilds with `make -C technical_report clean all`.

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
