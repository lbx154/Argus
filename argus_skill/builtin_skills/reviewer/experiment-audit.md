---
name: Experiment Audit
description: Audit experiment integrity before claiming results. Uses cross-model review (reviewer route) to check for fake ground truth, score normalization fraud, phantom results, dead code, and insufficient scope. Run after experiments complete and before paper claims are written.
category: reviewer
version: 1
scientist_model: gpt-5.5
created_at: 2026-06-03T00:00:00+00:00
source: ported from ARIS skills/experiment-audit/SKILL.md
---

## Title
Experiment Audit

## Description
Adapted from ARIS `experiment-audit`. Cross-model integrity check on experiment artifacts before any paper claim is written. The reviewer agent reads scripts/results/configs directly and emits a structured verdict; the executor never participates in the integrity judgment.

## When to use
- Experiments have produced result files (`experiments/**/summary.tsv`, `paper/artifacts/results_table.tsv`, `eval_results.jsonl`, etc.).
- The operator is about to write or has written paper claims that cite numbers.
- Before submission assurance, after any analysis-stage rebuild.

## When NOT to use
- No experiment artifacts exist yet — run experiments first.
- The paper is pure literature/survey with no empirical numbers.
- Re-auditing the same artifacts on a timer adds no signal (this skill is verdict-bearing; do not wrap in `/loop` or `CronCreate`).

## Why this exists
LLM agents can produce fraudulent experimental results through:
1. **Fake ground truth** — building synthetic "reference" from model outputs and reporting agreement as performance.
2. **Score normalization** — dividing metrics by the model's own max/mean to inflate scores toward 1.0.
3. **Phantom results** — claiming numbers from files that don't exist or functions never called.
4. **Insufficient scope** — calling a 2-scene pilot "comprehensive".
5. **Dead code** — eval functions defined but never called; metrics that look implemented but have zero coverage.

These are failure modes of optimising agents that lack integrity constraints. This skill adds the constraint.

## Core principle
**Executor collects file paths. Reviewer reads code and judges. Executor never participates in the integrity judgment.** Cross-model independence is required: invoke the configured reviewer route (typically `gpt-5.5` with `xhigh` reasoning effort), not the engineer route.

## Structural-gate contract (BLOCKING at analysis / review / submission)
The `experiment_audit` structural gate requires `paper/EXPERIMENT_AUDIT.md` AND `paper/EXPERIMENT_AUDIT.json` to exist at those stages, with the JSON exposing:
- `integrity_status` ∈ {`pass`, `warn`, `fail`}
- `checks` containing the five required keys: `gt_provenance`, `score_normalization`, `result_existence`, `dead_code`, `scope` (each `{status, details}`), plus `eval_type`
- `auditor` field set (so a hand-edited "PASS" can be traced to a real reviewer call)

A missing or malformed audit blocks the round. The gate does not score WHAT the verdict is (that's the reviewer's call); it only enforces that the audit exists, was machine-readable, and covers every checkpoint.

## How to solve

### Step 1: Collect artifacts (executor)
List paths without reading or summarising content:
- Evaluation scripts: `*eval*.py`, `*metric*.py`, `*benchmark*.py` under `code/`, `scripts/`, `experiments/`
- Result files: `*.json`, `*.tsv`, `*.csv` under `experiments/`, `paper/artifacts/`
- Experiment tracker: `experiments/EXPERIMENT_TRACKER.md`, `experiments/EXPERIMENT_LOG.md` if present
- Paper claims: `research/NARRATIVE_REPORT.md`, `paper/CLAIM_GRAPH.json`, `paper/main.tex`, `paper/sections/*.tex`
- Config files: `*.yaml`, `*.toml`, `*.json` configs with metric / dataset definitions

### Step 2: Call the reviewer route
Invoke the reviewer with the exact prompt below. The reviewer reads the files directly (sandbox `read-only`, project root as `cwd`).

```
You are an experiment integrity auditor. Read ALL files listed below
and check for the following fraud patterns.

Files to read:
- Evaluation scripts: [paths]
- Result files: [paths]
- Experiment tracker: [paths]
- Paper claims: [paths]
- Config files: [paths]

## Audit checklist

### A. Ground truth provenance
For each evaluation script:
1. Where does "ground truth" / "reference" / "target" come from?
2. Is it loaded from the DATASET, or generated/derived from MODEL OUTPUTS?
3. If derived: is it explicitly labelled as proxy evaluation?
4. Are official eval scripts used when available for this benchmark?
FAIL if: GT is derived from model outputs without explicit proxy labelling.

### B. Score normalization
For each metric computation:
1. Is any metric divided by max/min/mean of the model's OWN output?
2. Are raw scores reported alongside any normalised scores?
3. Are any scores suspiciously close to 1.0 or 100%?
FAIL if: normalisation denominator comes from prediction statistics.

### C. Result file existence
For each claim in the paper/narrative:
1. Does the referenced result file actually exist?
2. Does the claimed metric key exist in that file?
3. Does the claimed NUMBER match what's in the file?
4. Is the experiment tracker status DONE (not TODO/IN_PROGRESS)?
FAIL if: claimed results reference nonexistent files or mismatched numbers.

### D. Dead code detection
For each metric function defined in eval scripts:
1. Is it actually CALLED in any evaluation pipeline?
2. Does its output appear in any result file?
WARN if: metric functions exist but are never called.

### E. Scope assessment
1. How many scenes/datasets/configurations were actually tested?
2. How many seeds/runs per configuration?
3. Does the paper use words like "comprehensive", "extensive", "robust"?
4. Is the actual scope sufficient for those claims?
WARN if: scope language exceeds actual evidence.

### F. Evaluation type classification
Classify each evaluation as:
- real_gt: uses dataset-provided ground truth
- synthetic_proxy: uses model-generated reference
- self_supervised_proxy: no GT by design
- simulation_only: simulated environment
- human_eval: human judges

## Output format
For each check (A–F), report:
- Status: PASS | WARN | FAIL
- Evidence: exact file:line references
- Details: what specifically was found

Overall verdict: PASS | WARN | FAIL

Be thorough. Read every eval script line by line.
```

### Step 3: Parse reviewer response, write report

Write `paper/EXPERIMENT_AUDIT.md` (human-readable):

```markdown
# Experiment Audit Report

**Date**: <UTC date>
**Auditor**: reviewer route, xhigh reasoning (cross-model, read-only)
**Project**: <project name>

## Overall verdict: PASS | WARN | FAIL
## Integrity status: pass | warn | fail

## Checks
### A. Ground truth provenance: PASS|WARN|FAIL
<details + file:line evidence>

### B. Score normalization: PASS|WARN|FAIL
<details>

### C. Result file existence: PASS|WARN|FAIL
<details>

### D. Dead code detection: PASS|WARN|FAIL
<details>

### E. Scope assessment: PASS|WARN|FAIL
<details>

### F. Evaluation type: real_gt | synthetic_proxy | ...
<classification + evidence>

## Action items
- <specific fixes for WARN / FAIL>

## Claim impact
- C1: supported | needs_qualifier | unsupported
- C2: ...
```

Write `paper/EXPERIMENT_AUDIT.json` (machine-readable; structural-gate reads this):

```json
{
  "date": "2026-06-03",
  "auditor": "reviewer-route-xhigh",
  "overall_verdict": "warn",
  "integrity_status": "warn",
  "checks": {
    "gt_provenance":      {"status": "pass", "details": "..."},
    "score_normalization": {"status": "warn", "details": "..."},
    "result_existence":    {"status": "pass", "details": "..."},
    "dead_code":           {"status": "pass", "details": "..."},
    "scope":               {"status": "warn", "details": "..."},
    "eval_type": "real_gt"
  },
  "claims": [
    {"id": "C1", "impact": "supported"},
    {"id": "C2", "impact": "needs_qualifier"}
  ]
}
```

### Step 4: Print summary

```
🔬 Experiment Audit Complete

  GT Provenance:       ✅ PASS — real dataset GT used
  Score Normalization: ⚠️ WARN — boundary metric uses self-reference
  Result Existence:    ✅ PASS — all files exist, numbers match
  Dead Code:           ✅ PASS — all metric functions called
  Scope:               ⚠️ WARN — 2 scenes, paper says "comprehensive"

  Overall: ⚠️ WARN

  See paper/EXPERIMENT_AUDIT.md for details.
```

## Integration with the argus pipeline
- Run automatically after analysis stage produces `paper/RESULTS_REPORT.md`.
- Read by Paper Review Revision Loop: any `integrity_status == "fail"` becomes a critical revision item.
- Read by EMNLP Paper Drafting: when `integrity_status == "fail"`, affected claims gain a footnote noting the audit concern.
- Never blocks via advisory channel — the **structural** gate blocks only when the audit artifact is missing/malformed. The audit's own verdict (warn/fail) flows through the reviewer, not the harness.

## Key rules
- **Reviewer independence**: executor collects paths; reviewer judges. The reviewer route must be different from the engineer route (same family, different reasoning effort / config OK).
- **Cross-model where possible**: when the configured reviewer route is the same model family as the engineer, prefer `xhigh` reasoning effort to reduce shared-failure-mode risk.
- **File-as-switch**: the audit artifact is the contract surface. A missing artifact is "audit never ran", not "audit passed".
- **Honest about limits**: this skill catches common patterns, not all possible fraud. Safety net, not guarantee.

## Response shape
- Return paths to `paper/EXPERIMENT_AUDIT.md` and `.json`.
- Print the verdict summary as shown in Step 4.
- If FAIL: explicitly list the failing checks and the claims they affect.

## Acknowledgements
Adapted from ARIS `experiment-audit` (community-reported integrity issues #57, #131 where executor agents created fake ground truth and self-normalised scores).
