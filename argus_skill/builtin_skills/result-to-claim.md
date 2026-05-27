---
name: result-to-claim
description: "After experiments complete, judge which claims results support, which they don't, and what evidence is missing. Routes to next action: pivot, supplement experiments, or confirm and proceed to paper writing."
category: research-integrity
version: "1.0"
scientist_model: gpt-5.4
created_at: "2025-07-27"
---

# Result-to-Claim Gate

Experiments produce numbers; this gate decides what those numbers *mean*.

## When to Use

- After a set of experiments completes (main results, not just sanity checks)
- Before committing to claims in a paper
- When results are ambiguous and you need an objective assessment

## Workflow

### Step 1: Collect Results

Gather experiment data from available sources:

1. **EXPERIMENT_LOG.md / EXPERIMENT_TRACKER.md**: results table with baselines
2. **Result files**: `*.json`, `*.csv` in `results/`, `outputs/`, `logs/`
3. **Research contract**: intended claims and experiment design
4. **Config files**: what was actually tested (hyperparams, seeds, datasets)

Assemble:
- What experiments were run (method, dataset, config)
- Main metrics and baseline comparisons (deltas)
- The intended claim these experiments were designed to test
- Any known confounds or caveats

### Step 2: Independent Judgment

Use a separate reasoning pass (high effort) to evaluate:

```
RESULT-TO-CLAIM EVALUATION

Intended claim: [the claim these experiments test]

Experiments run:
[list experiments with method, dataset, metrics]

Results:
[key numbers, comparison deltas, significance]

Baselines:
[baseline numbers and sources — reproduced or from paper]

Known caveats:
[confounding factors, limited datasets, missing comparisons]

Evaluate:
1. claim_supported: yes | partial | no
2. what_results_support: what the data actually shows
3. what_results_dont_support: where the data falls short
4. missing_evidence: specific evidence gaps
5. suggested_claim_revision: strengthen, weaken, or reframe?
6. next_experiments_needed: specific experiments to fill gaps
7. confidence: high | medium | low
```

### Step 3: Check Experiment Integrity

If `EXPERIMENT_AUDIT.json` exists:
- Read `integrity_status`
- If `fail`: downgrade confidence to "low", tag claims as `[INTEGRITY CONCERN]`
- If `warn`: tag claims as `[INTEGRITY: WARN]`

If no audit exists: label verdict as "provisional — no integrity audit run"

### Step 4: Route Based on Verdict

#### `no` — Claim not supported
1. Record postmortem: what was tested, what failed, hypotheses for why
2. Decide: pivot to next idea or try alternative approach
3. Update pipeline state

#### `partial` — Claim partially supported
1. Update the working claim to reflect what IS supported
2. Record the gap
3. Design supplementary experiments to fill evidence gaps
4. Re-run result-to-claim after supplementary experiments complete

#### `yes` — Claim supported
1. Record confirmed claim
2. If ablation studies incomplete → trigger ablation-planner
3. If all evidence is in → ready for paper writing

### Step 5: Output

```markdown
## Result-to-Claim Verdict

**Claim**: [the intended claim]
**Verdict**: yes | partial | no
**Confidence**: high | medium | low
**Integrity**: pass | warn | fail | unavailable

### What Results Support
[specific supported conclusions]

### What Results Don't Support
[where data falls short]

### Missing Evidence
[specific gaps]

### Suggested Claim Revision
[how to reframe if needed]

### Next Steps
- [specific action items]
```

## Rules

- The evaluator judges objectively — do not inflate claims beyond what data supports
- A single positive result on one dataset does not support a general claim
- If confidence is low, treat as inconclusive — add experiments rather than committing
- Always record the verdict and reasoning, regardless of outcome
- Multiple rounds of `partial` on the same claim → consider narrowing scope

## Integration

- Runs after `agent-research-benchmark-runner` completes experiments
- Reads from `experiment-audit` if available
- Routes to `ablation-planner` (if yes + ablations needed)
- Routes to `emnlp-paper-drafting` (if all claims confirmed)
- Routes back to `research-brief-to-experiment-plan` (if partial/no)
