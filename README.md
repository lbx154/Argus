<h1 align="center">Argus: Autonomous Research Generation and Understanding System</h1>

<p align="center"><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></p>

> **Every run expands the frontier.**

<p align="center">
  <img src="technical_report/figures/master_spine.png" alt="The Argus technical spine: an unknown out-of-distribution objective enters a dense-intelligence runtime driven by Manager, Planner, Engineer, and Reviewer; explicitly verified work passes an evidence gate; the gate updates durable runtime state — memory, skills, tools, verifiers, routing, evaluations; and the enlarged runtime meets the next unknown task from a higher floor" width="100%">
</p>

Argus is an autonomous-research runtime that keeps decision, execution, and
verification coupled over long horizons. Four persistent, model-driven roles
sustain **dense intelligence** across a continuous loop; every run's
explicitly verified **evidence** updates durable **runtime state** — memory,
skills, tools, verifiers, routing, and evaluations — with the model's parameters
held fixed; and the enlarged runtime meets the next out-of-distribution
objective from a higher floor. That single spine — dense intelligence, evidence,
runtime evolution, an expanding frontier — is what this project is built and
measured on.

## Dense Intelligence for Long-Horizon Research

Strong models are episodic; long-horizon research spans thousands of coupled
decisions across hours or days. Argus keeps that work connected by running four
model-driven roles over persistent project state: Manager fixes intent and
lifetime, Planner schedules, Engineer builds and experiments, and Reviewer
independently checks work when required or requested. Allowed low-risk bounded
work may instead use explicit Engineer self-review.

This persistent coupling of judgment, execution, and verification is what we
call **dense intelligence**. `rho_DI(T)` is only a conceptual description of
that design goal, not a reported benchmark or a universal superiority score.

## From Work to Evidence to Runtime Evolution

Each run records checked evidence and its completion source. Engineer self-review
is allowed for low-risk bounded work; vertical policy, `stage_closing` /
`review:required`, or an Engineer request invokes an independent Reviewer. The
result updates persistent memory, skills, tools, verifiers, routing, and
evaluations: `H(t+1) = U(H(t), trajectory, evidence)`.

Ownership is scoped. On independently reviewed missions, the **Reviewer
certifies** memory and skill work it did not author. Tools are **operator-owned**;
verifiers are **Planner-owned** with the Reviewer **feedback-only**; routing is
Manager-committed; evaluations are Planner-authored and scheduler-committed.

Runtime evolution **does not require online parameter training**: base-model
weights stay fixed (`theta_(t+1) = theta_t`). It also does not guarantee that every run adds capability. Results may fail, add only a negative finding, or
later be revised or retired. The claim is limited to evidence-gated, reusable
runtime state—not monotonic improvement.

## Evidence from the Frontier

Argus keeps a live public record at
[argusbot.cn/results.html](https://argusbot.cn/results.html) and
[argusbot.cn/research.html](https://argusbot.cn/research.html). Human records,
human-authored baselines, and paper-reported best results are the primary
references; the machine-readable snapshots are committed in
[`technical_report/evidence/website_results.json`](technical_report/evidence/website_results.json)
and
[`technical_report/evidence/paper_inventory.json`](technical_report/evidence/paper_inventory.json).

| Arena | Protocol | Argus result | Primary reference | Evidence tier |
|---|---|---:|---|---|
| NVIDIA SOL-ExecBench | B200 · 101 kernels | Global #6 · 2× #1 · 7 top-3 | Public leaderboard rank | Website snapshot |
| nanochat · B200 | 5 min · 1×B200 · 426 attempts | **0.9636 BPB** | Human SOTA: 0.9646 | Artifact digest |
| nanochat · H100 | 5 min · 1×H100 · 37 mechanisms | **0.9855 BPB** | Human SOTA: 0.9879 | Website snapshot |
| nanoGPT speedrun | 8×H100 · N=10 | **79.77 s** | Same-device human #83: 80.18 s | Artifact digest |
| AARRI-Bench | 82 research-intern tasks | **63/82 · 76.8%** | Paper-reported best: 68.3% | Website snapshot |
| Arbor · RUC NLPIR | Math-Reasoning Data | **28.0 gap** | Arbor: 20.83 | Website snapshot |

Each row is a scoped claim under its own protocol and unit; the arenas measure
different quantities and are never cross-normalized. Two rows — the nanoGPT
speedrun and nanochat on B200 — carry committed artifact-digest records: a ten-run
verifier line (`valid=true`, `p=0.004007`, `79.77±0.06 s`, `seal=ok`) and a
frozen-scorer, one-seed `MEAN_VAL_BPB=0.963634`. This repository stores their
logical artifact IDs and SHA-256 digests, not the artifact bytes. The other four
rows are website snapshots and are labeled accordingly.

Separately, the public research portfolio contains **41 de-duplicated papers
across six programs**: 35 manuscripts and 6 drafts, spanning cognitive bias in
LLMs (9), multimodal and vision-language models (16), LLM agent methods (5),
efficiency/compression/decoding (7), world models (2), and state trace and
auditability (2). This is an output inventory compared only to human-authored
literature, not an acceptance count; Argus makes no claim that any of the 41
papers have been accepted. Every benchmark claim is treated as a protocol-scoped
measurement that retains its benchmark version, hardware and software environment,
baseline definition, commands, exit status, repeated-run statistics where
applicable, and the hashes of the artifacts that support it.

## How Argus Makes the Loop Real

The runtime is organized into three cooperating planes: a **control plane** for
intent, planning, scheduling, budgets, and daemon lifecycle; an **execution
plane** for search, code, experiments, and independent review; and an **evidence
plane** of durable state (`events.jsonl`, `checkpoint.json`, the journal,
evidence bundles, and figure manifests). Four model-driven roles act across those
planes through explicit interfaces:

| Role · plane | System responsibility | Decision boundary |
|---|---|---|
| **Manager** · control | Front door for operator intent; selects lifetime and vertical; owns pipeline-stage transitions | Other roles may recommend a stage change but cannot apply it |
| **Planner (L4)** · control | Builds and revises the work backlog; schedules certification work when required | Produces structured tasks and project-level planning verdicts |
| **Engineer (L1)** · execution | Executes one bounded round using real files, tools, searches, and hardware | Produces artifacts and selects `review=skip|required`; `skip` is accepted only when self-review is enabled and independent review is not mandatory |
| **Reviewer (L2)** · execution → evidence | Independently inspects artifacts and logs when required or requested | Returns `done`, `continue`, or `blocked`; mandatory for stage-closing and vertical-required review |

The append-only event tape is the canonical timeline, so an operator can move
from a published number to its mission, round, review verdict, command record,
and artifact set without trusting a prose summary. A mission moves through a
durable lifecycle — an operator request is interpreted, planned into backlog
items, atomically claimed, executed through an Engineer self-review or
Engineer–Reviewer path, and returned as complete, blocked, paused, or ready for
more planning — and after a controlled
restart the daemon resumes the same campaign only when its persisted identity
matches the current objective, vertical, and lineage. The runtime treats
reliability as a first-class concern: one host-global daily USD cap with atomic
call reservations, plus host-concurrency limits; bounded retry and backoff for backend failures
instead of success-looking fallbacks; a shared `CHECKPOINT.md` edited by the
Engineer and, when invoked, corrected by the Reviewer between fresh role
sessions; and credential redaction before events and artifacts enter review.
Evaluation inputs may be randomized when a fixed known input distribution would
otherwise permit hard-coded optimization. These mechanisms govern execution; they
never score novelty, choose ideas, or infer completion from keywords. Scientific
quality stays a structured agent decision grounded in the artifacts of the run.

## Quick Start

The canonical public beta is the native npm package. Install one backend first;
for GitHub Copilot subscribers:

```bash
npm install -g @github/copilot
copilot login
npm install -g @argusevolve/argus@beta
argus --setup --non-interactive \
  --backend copilot \
  --accept-house-rules
argus
```

For development, use the source fallback:

```bash
git clone https://github.com/lbx154/argus-skill.git
cd argus-skill
python -m venv .venv
. .venv/bin/activate
pip install -e .
argus --setup
```

Install `pip install -e '.[quant]'` when using the quant analysis, backtest,
or K-line chart helpers. Heavy engines such as qlib remain project-managed.

The setup wizard creates a trusted baseline machine-policy prompt when none
exists. It never overwrites operator-authored prompts. Customize the generated
directive when the machine has additional house rules:

```bash
${EDITOR:-vi} ~/.argus-skill/special_prompts/10-house-rules.md
```

Start a continuous project from its working directory:

```bash
mkdir -p ~/research/world-models
cd ~/research/world-models
argus --daemon --continuous \
  --objective "World Model for Agent Action Selection"
```

Use `argus` for the interactive TUI cockpit, `argus --daemon-fg` for a
supervised foreground worker, and `argus --daemon` for persistent unattended
operation. Inspect a running project with `argus --status`, `--watch`, and
`--follow`. Argus can also run under a
user-level service manager for persistent operation; controlled replacement
preserves the campaign identity and never silently re-plans an active objective
during an upgrade.

For a source checkout, updating remains one-command at launch:

```bash
git pull --ff-only
pip install -e .  # refresh dependencies and entry points when they changed
argus
```

Every `argus` launch compares the current local source fingerprint with the
running local WebAPI. It reuses a matching process, starts a missing one, and
gracefully replaces an outdated process only after proving that the endpoint is
owned by this Argus installation. It never signals an unrelated port occupant.
The same launch also finds stale project daemons and schedules a drain-and-resume
upgrade: an active mission reaches its normal reviewed boundary before the new
daemon takes over, with no mid-mission `SIGKILL`.

Argus targets four interchangeable agent-CLI backends:

| Backend | Configuration value | Installation | Authentication |
|---|---|---|---|
| GitHub Copilot CLI | `copilot` | `npm install -g @github/copilot` (Node.js ≥ 22) | Interactive GitHub device authorization |
| OpenAI Codex CLI | `codex` (default) | `npm install -g @openai/codex@latest` | `subscription_cli` or explicit `model_api`; stable `>=0.128.0` |
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` | Interactive login |
| OpenCode | `opencode` | `curl -fsSL https://opencode.ai/install \| bash` | `opencode auth login` or provider environment variables |

Set `ARGUS_SKILL_RUNNER_BACKEND`, or switch the backend and model from the
cockpit without restarting the project.

The complete platform, version, authentication-mode, noninteractive setup, and
exit-code contract is in
[`docs/setup-and-support.md`](docs/setup-and-support.md). Setup and
`argus --doctor` share the same readiness check. Global Git identity and
backend-owned authentication files are never changed without explicit opt-in.

OpenCode model IDs use its `provider/model` form. When Argus has only a bare
model name configured, the OpenCode backend defers to OpenCode's own selected
model instead of passing an invalid `--model` value.

## Technical Report, Limitations, and Provenance

The architecture, role interfaces, mission state machine, evidence methodology,
runtime-evolution formalism, six public arenas, and 41-paper portfolio are
documented in full in:

**[Argus: Autonomous Research Generation and Understanding System —
Technical Report 0.3](technical_report/argus-technical-report.pdf)**

The LaTeX source is under [`technical_report/`](technical_report/) and rebuilds
with `make -C technical_report clean all`.

Argus is under active development, and its guarantees are deliberately bounded.
Research quality remains limited by the underlying models, tools, data, and
compute. Completion is a fallible model judgment: either explicit Engineer
self-review on an allowed low-risk bounded mission or an independent Reviewer
verdict on required/requested paths. Four of the six
public arena results do not yet have artifact-digest corroboration, and even the
two corroborated rows reference external project artifacts rather than storing
their bytes here. Benchmark integrity must be engineered separately for each
protocol, continuous operation has real compute and provider cost, and the
current evidence system provides content hashes and provenance manifests, not
cryptographic result signing. Treat every performance number as a scoped result
under its stated protocol, not as a universal capability guarantee.

Package metadata declares the project under the MIT license. It builds on
[skill-agent](https://github.com/lbx154/skill-agent) for skill matching and
distillation, and on [ArgusBot](https://github.com/waltstephen/ArgusBot) for the
reviewer loop and CLI runner, with vendored provenance and license material under
[`argus_skill/agent_cli/`](argus_skill/agent_cli/).
