---
name: Idea Feasibility De-Risk (Measured Signal Evidence)
description: For a research checklist that calls for a measured signal screen, run one <=10 min/<=$1 minimal experiment on a model/data this box can actually run. Emit research/SIGNAL_DERISK_LOG.txt plus research/SIGNAL_DERISK.json, pivot on a dead signal, and give the Reviewer reproducible evidence. The Planner may replace this checklist item for research shapes where performance metrics are inapplicable.
category: paper-ideation
version: 1
created_at: 2026-06-27T00:00:00+00:00
---

# Idea Feasibility De-Risk — a reality screen before you spend plan + run budget

## Why this skill exists

The research stage today passes on **form**: the reviewer checks problem clarity,
a connected timeline, source diversity, a real-search audit; the shell checks
only test that files exist. **Nothing checks whether the idea is alive on THIS
machine** — whether its core signal actually moves on a model/data you can
really run.

A beautifully-cited brief can still wrap a dead idea. Real example: a TTS-safety
idea had a polished brief, solid citations, a complete GO/NO-GO with pivot
conditions — research passed clean. But the idea was **dead**: the only
available frontier model refuses every harmful prompt, so the "safety signal"
never moves. That was discovered **3.5 hours and $26 later, in the run stage**.

This skill buys a ≤10-min / ≤$1 reality screen *before* plan+run discover the
death. It is a **provenance/feasibility gate, NOT a science-quality verdict** —
it proves the method beats a reproduced baseline on a cheap slice (or its
faithful proxy), not that the paper is good. The reviewer still judges whether
the idea is worth pursuing.

## When to use

Run this at the **END of the research stage**, after `research/RESEARCH_BRIEF.md`
and `research/GO_NO_GO.md` exist, when the active `research.signal_derisk`
checklist item asks for the default measured-signal evidence. Re-run after every
pivot. If the Planner authored a different evidence contract, follow that
contract instead of inventing inapplicable metrics.

## Step 1 — Lock exactly ONE candidate idea + its core measurable signal

From `research/RESEARCH_BRIEF.md` (and `research/IDEA_REJECTION_LOG.md`) pick the
single idea that will go to `plan`. Name:
- `idea_id` — a short stable id for it.
- `metric_name` — the ONE core measurable signal the whole thesis rests on
  (e.g. `attack_success_rate`, `exact_match`, `refusal_rate`, `pass@1`).
- `success_direction` — `"higher"` or `"lower"`: which way the metric must move
  for the idea to be working (e.g. a defense should drive attack-success-rate
  DOWN → `"lower"`).

If you cannot name one core metric and a direction, the idea is not crisp enough
to plan — sharpen the brief first.

## Step 2 — Read the local deployment constraints; decide what's REALLY runnable

**HARD: do not pick a model from memory or aspiration.** This turn's runtime
context already contains the real constraints — read these blocks and choose
only from them:
- `## GPU Resource Allocation` — the actual CUDA devices + VRAM ceiling you may
  use.
- `## Available APIs` — the concrete vault routes with real `model` / `base_url`,
  and the vault path to load the key from in code. Use a route that exists.
- `## Operator Directives` / special-prompt blocks — any model/budget/train-free
  constraint the operator set.

Pick the model + a tiny **real** data slice (10–50 examples is plenty) the box
can run NOW. **If the idea's premise depends on a behaviour the available model
will not exhibit — e.g. a safety signal that needs the model to comply with
harmful prompts, but the only available frontier API refuses 100% of them —
that itself is a FAIL.** Record `model_id`, `model_source` (the vault route name
or a local weights path — proving it is box-runnable, not aspirational),
`data_source`, `n_examples`.

## Step 3 — Design the judgemental minimal experiment

Two conditions on the SAME data and SAME budget:
- `baseline` — a **reproduced, competitive baseline**: the current strong method
  for this task (or a faithful re-implementation), NOT a crippled/no-op control.
  Beating a straw-man does not count.
- `proposed` — your idea's method applied.

Make it **falsifying**: write down, BEFORE running, the observation that KILLS
the idea (the method does NOT beat the baseline; `proposed` is within noise of
`baseline`; the model can't produce the behaviour at all). Declare
`min_meaningful_delta` — the smallest margin by which `proposed` must beat
`baseline` to count as a real win (in the `success_direction`) — up front, not
post-hoc. Keep the SCREEN cheap: wall-clock ≤ 600 s, cost ≤ $1.00, smallest N
and cheapest route that still separates the two conditions. If the method is
training-based, de-risk on the cheapest FAITHFUL proxy that still shows the
method beating the baseline (a tiny slice, a short ≤3 h LoRA run per the GPU
house rules, or the inference-time component alone); the FULL win is confirmed
by the ≤8 h main experiment in `plan`/`run`, not here.

## Step 4 — RUN IT FOR REAL; capture the raw commands + outputs

Append **every command AND its verbatim stdout/stderr** to
`research/SIGNAL_DERISK_LOG.txt`. The log MUST contain the real invocations that
hit the model/API/data (a real client/curl call, a real eval over the N rows) so
a reviewer can `grep` them — exactly like the existing "Real-search audit"
expects real `curl` to arxiv. For anything over ~30 s, launch it with
`python -m argus_skill.tools.subagent submit` and capture its output into the log.

## Step 5 — Emit `research/SIGNAL_DERISK.json`

Write the verdict object. **Every number is COMPUTED from the captured run,
never typed from expectation.** Schema:

```json
{
  "schema_version": 1,
  "idea_id": "tts-safety-decoding-defense",
  "metric_name": "attack_success_rate",
  "success_direction": "lower",
  "model_id": "gpt-5.5",
  "model_source": "vault:coproxy",
  "data_source": "advbench_subset_40.jsonl",
  "n_examples": 40,
  "baseline_metric": 0.62,
  "proposed_metric": 0.31,
  "delta": -0.31,
  "min_meaningful_delta": 0.10,
  "signal_moved": true,
  "cost_usd": 0.18,
  "duration_s": 220,
  "log_path": "research/SIGNAL_DERISK_LOG.txt",
  "commands": [".venv/bin/python experiments/derisk/run_baseline.py --n 40", ".venv/bin/python experiments/derisk/run_proposed.py --n 40"],
  "verdict": "pass",
  "pivoted": false,
  "smoke_only": false,
  "notes": "Defense drives ASR 0.62 -> 0.31 on 40 AdvBench prompts; signal clearly moves in the success direction."
}
```

- `delta` must equal `proposed_metric - baseline_metric` (the validator
  re-checks; a hand-edited delta is caught).
- `commands` must be the exact commands also present verbatim in the log.

## Step 6 — Pass / fail + pivot rule

**PASS requires ALL of:** the signal moved; `baseline_metric != proposed_metric`
by at least `min_meaningful_delta` in the declared `success_direction`;
`cost_usd <= 1.0`; `duration_s <= 600`; `log_path` non-empty; `pivoted == false`.

**FAIL → PIVOT.** Set `verdict="fail"`, write the reason in `notes`, update
`research/RESEARCH_BRIEF.md` + `research/IDEA_REJECTION_LOG.md` with what died and
why, pick a new idea, and **re-run this skill**. Do NOT ask the reviewer to
advance research on evidence that contradicts the active checklist.

A legitimate exemption exists for pure infra/wiring screens with no single metric
(e.g. you only proved the eval harness runs end-to-end): set `smoke_only=true`
with an explanation. A `smoke_only` screen waives the move/delta checks but NOT
the budget/log checks, and it may **NOT** later be cited as "the idea is alive".

## HARD BAN (anti-fraud)

**It is FORBIDDEN to fill `SIGNAL_DERISK.json` from memory, estimate, or "this
should work".** Every number must trace to a real command + output in
`research/SIGNAL_DERISK_LOG.txt`. A `verdict="pass"` without a matching real run
is fabrication, and it gets caught two independent ways:

1. **Consistency/provenance diagnostic** — run `python -m
   argus_skill.skills.signal_derisk validate --project-root . --derisk
   research/SIGNAL_DERISK.json`. A missing/empty log, a degenerate
   `baseline==proposed`, an unmoved or wrong-direction signal, an inconsistent
   `delta`, an over-budget cost/time, or a `smoke_only="false"` soft-exempt exits
   non-zero. Fix the evidence; do not treat the command as a substitute for the
   Reviewer's task-aware judgment.
2. **Human-level** — the reviewer's HARD "Signal de-risk audit" dimension greps
   `SIGNAL_DERISK_LOG.txt` for the listed `commands` and BLOCKs if the numbers
   have no real run behind them.

## Reviewer hook

When this measured-signal contract is active, the reviewer must not certify it
without `verdict="pass"` and a log whose contents actually back the numbers.

## What this skill is NOT

- NOT the full experiment matrix — that is the run stage. This is one cheap
  decisive screen.
- NOT a scientific-quality verdict — passing here means "the signal is real and
  moves", not "the paper is good". The reviewer still judges worth.
- NOT universally applicable. A theorem or other non-performance research task
  should use a Planner-authored evidence contract suited to that work.
