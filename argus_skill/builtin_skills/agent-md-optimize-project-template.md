---
name: "AGENTS.md Optimize Project Template"
description: "Copy-ready AGENTS.md template for a benchmark-optimization project — maximize/minimize one real metric on real hardware, correctness-gated, with no paper, no venue, and no literature-review pipeline."
---

## Title
AGENTS.md Optimize Project Template

## When to use
- Use this when the project's deliverable is a NUMBER, not a paper: push a single
  benchmark metric in the right direction (GPU kernel speed-of-light %, speedrun
  validation bits-per-byte, wall-clock time-to-target-loss) on real hardware.
- Use it for the optimize verticals: `kernel_engineering`, `kernelbench`,
  `speedrun`, `nanochat`, `nanogpt_speedrun`. These are lean metric/code loops
  with no literature review, no draft, no reviewer paper simulation, and no
  submission packaging. `kernel_engineering` adds explicit scope/environment/
  baseline/validation stages because a production kernel change must prove its
  toolchain and integration surface before claiming speed.

## When NOT to use
- Do not use this when the deliverable is a research paper / report. Use the
  Research stage playbooks instead.
- Do not bolt a paper pipeline (RESEARCH_BRIEF, literature review, LaTeX draft,
  venue/submission) onto an optimize project — that is the exact mis-routing this
  template exists to prevent.

## Copy-ready `AGENTS.md`

````markdown
# AGENTS.md

## Project contract
This workspace is a **benchmark-optimization** project, not a paper. The whole
deliverable is ONE real metric pushed in the right direction on real hardware:
maximize a GPU-kernel speed-of-light (SOL %) score, minimize a speedrun
validation bits-per-byte (val_bpb), or minimize the wall-clock time-to-target
training loss. Build the project as a lean optimize loop: setup → optimize →
measure → report. There is NO paper, NO venue, NO literature review, NO LaTeX
draft, and NO submission package — do not produce RESEARCH_BRIEF.md,
EXPERIMENT_PLAN.md (paper sense), `paper/`, exemplars, claim graphs, academic
language / layout reviews, or figure-2 prompts. Those are research-vertical
artifacts and are out of scope here.

**Correctness-gates everything.** A result that is wrong scores **zero**. A
faster kernel that produces incorrect outputs, a lower loss from a broken
evaluation, or a speedup measured against a mis-built baseline are all failures,
not wins. Always verify correctness FIRST (the harness's own correctness check
must pass), and only then report the optimized metric. Never trade correctness
for a better number.

**The metric must be a real measurement from a real run on real hardware.**
Numbers come from actually executing the scorer / harness on the allocated GPU,
not from estimates, hand-edited result files, hard-coded constants, mocked
timers, or a scorer you weakened to make the number look good. **Faking the
measurement — patching the harness to inflate the score, short-circuiting the
correctness check, caching a stale good number, or reporting a number you did
not actually measure — is the single worst thing you can do here and is strictly
forbidden.** If you cannot run the real benchmark (missing GPU, broken harness,
unavailable dependency), report that honestly and stop; do not fabricate a
score.

## Binding playbooks and completion contract
- Read the active vertical's `role_banner` (the optimize vertical your objective
  routes to: `kernel_engineering` / `kernelbench` / `speedrun` / `nanochat` /
  `nanogpt_speedrun`) — it
  states the metric, benchmark constraints, correctness rule, and stop condition
  for this task. Benchmark constraints never prove that matching hardware is
  currently accessible; verify the configured runner before making that claim.
- Before touching the scorer, inspect only the active vertical's Skill paths
  supplied by Argus and open a Skill whose description clearly matches this
  repository and objective. Read the current project's frozen harness directly;
  never import hardware assumptions, score anchors, commands, or worked traces
  from another vertical or historical run.
- At project setup, export the built-in skill markdown so you can read it
  directly:
  `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill --export-builtin-skills ./argus_builtin_skills`
- Use the active Argus package/source checkout supplied by the launcher. Prefer
  `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill ...` for Argus helper commands;
  the launcher injects `ARGUS_SKILL_PYTHON`, `ARGUS_SKILL_SOURCE_ROOT`, and
  `PYTHONPATH`. Do not hard-code host-specific paths.
- Completion is metric-based, not reviewer-paper-based: the task is done when the
  real benchmark, run on real hardware with the correctness gate passing, reports
  a metric that meets the vertical's stop condition (e.g. a target SOL %, a
  val_bpb floor, or a wall-clock target). Quote the actual measured number and
  the command that produced it as completion evidence.

## Optimize workflow
1. **Study the harness before optimizing.** Read the scorer / benchmark harness
   and understand exactly how the metric is computed, what the correctness check
   is, and what the roofline / speed-of-light ceiling is. You cannot optimize a
   metric you do not understand, and you cannot tell a real win from a measurement
   artifact without reading the scorer.
2. **Establish the real baseline.** Run the unmodified reference on the allocated
   GPU and record the baseline metric + that the correctness check passes. Every
   later number is relative to this real baseline.
3. **Optimize in correctness-gated increments.** For each change: write down the
   assumption / hypothesis (why you expect it to move the metric), apply it, run
   the real harness, confirm correctness still passes, and record the real
   measured score. Keep changes that help and that stay correct; revert changes
   that regress correctness or the metric.
4. **Saturate the hardware honestly.** Use the allocated GPU(s) fully; a slow,
   under-utilized run wastes the optimize loop. But never let "go faster" cross
   into "go wrong" — correctness first, speed second.
5. **Report real measurements only.** Maintain a simple optimize log of
   {change, assumption, correctness pass/fail, measured metric, command}. The log
   is evidence, not an optimization target — never hand-edit it to show a number
   you did not measure.

## Operator goal
- Primary objective: [write the target research problem and deliverable]
- Mission shape: benchmark optimization — push ONE real metric in the right
  direction on real hardware, correctness-gated. No paper, no venue, no
  literature review, no draft, no submission package.
- Success condition: the real benchmark, run on real hardware with the
  correctness gate passing, reports a metric that meets the vertical's stop
  condition; the measured number and the exact command that produced it are
  recorded as evidence.
- Non-goals: [write what must not be optimized, copied, or claimed]
- Allowed compute/API budget: [write limits and stop conditions]

## Allowed starting inputs
List every starting input before using it:

| Input | Source/path/URL | License/access | How it may be used | Why it is appropriate |
| --- | --- | --- | --- | --- |
| [input] | [source] | [status] | [allowed use] | [rationale] |

If an input is not listed here, treat it as unavailable until documented.

## Model/API and helper-code contract
1. Model and image credentials are operator capabilities, not project artifacts.
   The private vault is `~/.argus-skill/capabilities/model_api.json` or
   `ARGUS_SKILL_CAPABILITY_VAULT`; it should be mode `0600`. Do not open, print,
   summarize, copy, or commit its raw contents; only Argus route helpers/tools
   may load it at runtime.
2. Before model-backed work, run the secret-free status check:
   `"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill --model-api-status`.
3. Put reusable project wrappers under `code/` only when the task needs them;
   nothing is pre-seeded. Use Argus route loaders rather than hard-coded keys,
   base URLs, or model names.

## Role model
- Planner: picks the next highest-leverage optimization for the target metric and
  keeps the loop correctness-gated.
- Engineer: studies the scorer/harness/roofline, implements the optimization,
  runs the real benchmark on real hardware, and records the real measured metric.
- Reviewer: checks that correctness still passes, that the reported metric came
  from an actual run (not a fabricated or mis-measured number), and that the
  optimize log is honest.

## Operational safety
1. Work inside this project directory unless reading the active Argus
   source/package through the launcher-provided environment.
2. Never copy parent workspaces, the Argus repository, `.argus-skill`, `.cache`,
   model caches, or capability vaults into this project.
3. Model weight storage: put any downloaded checkpoint/dataset under `./models/`
   and point the HuggingFace / PyTorch caches there. Add `models/` to
   `.gitignore`. Each project owns its weights.
4. Keep API keys and capability-vault contents out of all artifacts.
5. Record meaningful decisions and real measured numbers in project files.
6. Preserve user edits and unrelated work.

## Forbidden shortcuts
- Do NOT fabricate, hand-edit, or hard-code the benchmark metric. Every reported
  number must come from an actual run of the real harness on real hardware.
- Do NOT weaken, bypass, or short-circuit the correctness check to make a number
  look better. A wrong result scores zero.
- Do NOT patch the scorer/harness to inflate the metric, cache a stale good
  number, or report a number you did not measure.
- Do NOT turn this into a paper: no RESEARCH_BRIEF, no literature review, no
  LaTeX draft, no venue/submission, no figure-2 prompts.
- Do NOT silently ignore failed commands, correctness regressions, or an
  unavailable benchmark — report honestly and stop rather than faking a score.

## Completion contract
A task is complete only when:
- the correctness gate passes on the real harness,
- the optimized metric is a real measurement from an actual run on real hardware,
- the measured number and the exact command that produced it are recorded as
  evidence,
- the optimize log is synchronized with what was actually run,
- the vertical's stop condition (target metric) is met, or remaining gaps are
  reported honestly without pretending the number was reached.
````

## Generality check
This template is optimize-vertical-specific but must stay project-neutral. It
must not contain host-specific Argus paths, a specific project title, a specific
result number, or a prior-workspace story.

## Coverage check
Before using the template, fill all bracketed placeholders and list allowed
inputs. Do not relax the correctness gate or the no-fabrication rule.
