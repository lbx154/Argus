---
name: experiment-audit
description: "Audit experiment integrity before claiming results. Checks for fake ground truth, score normalization fraud, phantom results, and insufficient scope. Use after experiments complete and before writing claims."
category: research-integrity
version: "1.0"
scientist_model: gpt-5.4
created_at: "2025-07-17"
---

# Experiment Audit: Integrity Verification

Audit experiment integrity before writing claims or submitting papers.

## Why This Exists

LLM agents can produce fraudulent experimental results through:
1. **Fake ground truth** — creating synthetic "reference" from model outputs, then reporting high agreement
2. **Score normalization** — dividing metrics by the model's own max to get 0.99+
3. **Phantom results** — claiming numbers from files that don't exist or functions never called
4. **Insufficient scope** — reporting 2-scene pilots as "comprehensive evaluation"
5. **Synthetic benchmark laundering** — presenting locally generated tasks, proxy graphs, or hand-written oracle cases as main benchmark evidence
6. **Tiny-model laundering** — presenting a bag-of-words scorer, lexical ranker, exact lookup/lookahead policy, or prompt wrapper as the proposed frontier method when available GPUs and field standards require training/adapting a stronger model

These are NOT intentional deception — they are failure modes of optimizing agents
that lack integrity constraints. This skill adds that constraint.

## Core Principle

**The executor collects file paths. An independent reviewer reads code and judges integrity.
The executor does NOT participate in integrity judgment.**

## Workflow

### Step 1: Collect Artifacts

Locate and list these files WITHOUT reading or summarizing their content:

```
Scan project directory for:
1. Evaluation scripts:    *eval*.py, *metric*.py, *test*.py, *benchmark*.py
2. Result files:          *.json, *.csv in results/, outputs/, logs/
3. Ground truth paths:    look in eval scripts for data loading (dataset paths, GT references)
4. Experiment tracker:    EXPERIMENT_TRACKER.md, EXPERIMENT_LOG.md
5. Paper claims:          NARRATIVE_REPORT.md, paper/sections/*.tex, PAPER_PLAN.md
6. Config files:          *.yaml, *.toml, *.json configs with metric definitions
```

**DO NOT summarize, interpret, or explain any file content.** Only collect paths.

### Step 2: Audit Checklist

For each category, the reviewer must check:

#### A. Ground Truth Provenance
1. Where does "ground truth" / "reference" / "target" come from?
2. Is it loaded from the DATASET, or generated/derived from MODEL OUTPUTS?
3. If derived: is it explicitly labeled as proxy evaluation?
4. Are official eval scripts used when available for this benchmark?

**FAIL if:** GT is derived from model outputs without explicit proxy labeling.

#### B. Score Normalization
1. Is any metric divided by max/min/mean of the model's OWN output?
2. Are raw scores reported alongside any normalized scores?
3. Are any scores suspiciously close to 1.0 or 100%?

**FAIL if:** Normalization denominator comes from prediction statistics.

#### C. Result File Existence
1. Does the referenced result file actually exist?
2. Does the claimed metric key exist in that file?
3. Does the claimed NUMBER match what's in the file?
4. Is the experiment tracker status DONE (not TODO/IN_PROGRESS)?

**FAIL if:** Claimed results reference nonexistent files or mismatched numbers.

#### D. Dead Code Detection
1. Is each metric function actually CALLED in any evaluation pipeline?
2. Does its output appear in any result file?

**WARN if:** Metric functions exist but are never called.

#### E. Scope Assessment
1. How many scenes/datasets/configurations were actually tested?
2. How many seeds/runs per configuration?
3. Does the paper use words like "comprehensive", "extensive", "robust"?
4. Is the actual scope sufficient for those claims?

**WARN if:** Scope language exceeds actual evidence.

#### F. Benchmark Reality
1. Does the main paper evidence use existing real benchmarks or official task/data releases?
2. Does `experiments/BENCHMARK_PROVENANCE.md`/`.json` list source URLs/repos, paper/citation/DOI, version/date, license/access, split/filtering, and task count?
3. Are any synthetic/local/proxy tasks used only for smoke tests and excluded from paper-facing claims?

**FAIL if:** synthetic/local/generated tasks are used as main benchmark evidence or benchmark provenance lacks real source pointers.

#### G. Model Scale and Frontier Fit
1. What model/backbone is actually trained or adapted?
2. Are parameter count, trainable parameters, dataset size, GPU plan, logs, checkpoint/adapter path, and evaluation command recorded?
3. Is the proposed method competitive with current field expectations, or only a tiny scorer/prompt wrapper?

**FAIL if:** the main proposed method is a tiny scorer/oracle/prompt wrapper despite available GPU capacity and no operator-approved scope downgrade.

#### H. Evaluation Type Classification
Classify each evaluation as:
- `real_gt`: uses dataset-provided ground truth
- `synthetic_proxy`: uses model-generated reference
- `synthetic_main_benchmark`: uses local/generated/synthetic tasks as final paper evidence
- `tiny_model_main_claim`: main proposed method is a tiny scorer or oracle policy without operator-approved scope downgrade
- `self_supervised_proxy`: no GT by design
- `simulation_only`: simulated environment
- `human_eval`: human judges

### Step 3: Write Report

Write `EXPERIMENT_AUDIT.md`:

```markdown
# Experiment Audit Report

**Date**: [today]
**Auditor**: argus-skill experiment-audit (independent review)
**Project**: [project name]

## Overall Verdict: [PASS | WARN | FAIL]

### A. Ground Truth Provenance: [PASS|WARN|FAIL]
[details + file:line evidence]

### B. Score Normalization: [PASS|WARN|FAIL]
[details]

### C. Result File Existence: [PASS|WARN|FAIL]
[details]

### D. Dead Code Detection: [PASS|WARN|FAIL]
[details]

### E. Scope Assessment: [PASS|WARN|FAIL]
[details]

### F. Benchmark Reality: [PASS|WARN|FAIL]
[details]

### G. Model Scale and Frontier Fit: [PASS|WARN|FAIL]
[details]

### H. Evaluation Type: [classification]
[classification + evidence]

## Action Items
- [specific fixes if WARN or FAIL]

## Claim Impact
- Claim 1: [supported | needs qualifier | unsupported]
- Claim 2: ...
```

Also write `EXPERIMENT_AUDIT.json` for machine consumption:

```json
{
  "date": "2025-07-17",
  "auditor": "argus-skill-experiment-audit",
  "overall_verdict": "pass|warn|fail",
  "checks": {
    "gt_provenance": {"status": "pass|warn|fail", "details": "..."},
    "score_normalization": {"status": "pass|warn|fail", "details": "..."},
    "result_existence": {"status": "pass|warn|fail", "details": "..."},
    "dead_code": {"status": "pass|warn|fail", "details": "..."},
    "scope": {"status": "pass|warn|fail", "details": "..."},
    "benchmark_reality": {"status": "pass|warn|fail", "details": "..."},
    "model_scale_frontier_fit": {"status": "pass|warn|fail", "details": "..."},
    "eval_type": "real_gt|synthetic_proxy|..."
  },
  "claims": [
    {"id": "C1", "text": "...", "impact": "supported|needs_qualifier|unsupported"}
  ]
}
```

### Step 4: Print Summary

```
🔬 Experiment Audit Complete

  GT Provenance:      ✅ PASS — real dataset GT used
  Score Normalization: ⚠️ WARN — boundary metric uses self-reference
  Result Existence:    ✅ PASS — all files exist, numbers match
  Dead Code:           ✅ PASS — all metric functions called
  Scope:              ⚠️ WARN — 2 scenes, paper says "comprehensive"

  Overall: ⚠️ WARN
  
  See EXPERIMENT_AUDIT.md for details.
```

## Integration with Pipeline

This skill runs automatically after experiments complete and before paper drafting:

```
benchmark-runner → results ready
    ↓
experiment-audit (automatic, advisory)
    ├── PASS  → continue normally
    ├── WARN  → print warning, continue, tag claims as [INTEGRITY: WARN]
    └── FAIL  → print alert, continue, tag claims as [INTEGRITY CONCERN]
    ↓
paper-drafting → proceeds with integrity tags visible
```

**Never blocks the pipeline.** Even on FAIL, the pipeline continues — but claims
carry visible integrity tags that downstream skills (paper-drafting, submission-gate) can see.

## Key Rules

- **Reviewer independence**: executor collects paths, reviewer judges. Period.
- **Never block**: warn loudly, never halt the pipeline.
- **File-as-switch**: no EXPERIMENT_AUDIT.md = skill was never run = zero impact.
- **Cross-model preferred**: use a different model for review when possible.
- **Honest about limits**: catches common patterns, not all possible fraud.
