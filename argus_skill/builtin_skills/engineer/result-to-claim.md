---
name: result-to-claim
description: "After experiments complete, judge which claims results support, which they don't, and what evidence is missing. Routes to next action: pivot, supplement experiments, or confirm and proceed to paper writing."
category: research-integrity
version: "1.0"
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
7. scenario_scope: the narrow problem setting or constraint where the method still has a coherent role
8. comparison_boundary: which omitted or losing comparisons limit the claim, and how to state that boundary without turning the paper into a self-rejection
9. contribution_after_reframe: operational benefit, mechanism insight, diagnostic value, protocol contribution, or scenario-specific trade-off that remains supported
10. confidence: high | medium | low
```

### Step 3: Check Experiment Integrity

If `EXPERIMENT_AUDIT.json` exists:
- Read `integrity_status`
- If `fail`: downgrade confidence to "low", tag claims as `[INTEGRITY CONCERN]`
- If `warn`: tag claims as `[INTEGRITY: WARN]`

If no audit exists: label verdict as "provisional — no integrity audit run"

### Step 4: Route Based on Verdict

#### `no` — Claim not supported
1. Record what was tested, what the evidence rejects, and which broad claim is unsafe.
2. Before pivoting, check whether the evidence supports a narrower scenario-scoped paper: a bounded diagnostic result, method characterization, protocol/evidence-boundary study, negative result for a specific candidate, or status report.
3. If a coherent scenario remains, rewrite the claim around that scenario instead of saying the method is generally bad.
4. If no coherent scenario or contribution remains, pivot to the next idea or try an alternative approach.
5. Update pipeline state.

#### `partial` — Claim partially supported
1. Update the working claim to reflect what IS supported.
2. Choose a concrete scenario/pain point that the completed evidence actually addresses.
3. Record the comparison boundary: absent baselines, stronger baselines, missing ablations, or non-decisive metrics.
4. Design supplementary experiments to fill evidence gaps when they are necessary for the chosen scenario.
5. Re-run result-to-claim after supplementary experiments complete.

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

### Scenario-Scoped Paper Thesis
[core pain point, selected scenario, why common alternatives are mismatched there, what the method contributes, and what boundary remains]

### Next Steps
- [specific action items]
```

## Rules

- The evaluator judges objectively — do not inflate claims beyond what data supports
- A single positive result on one dataset does not support a general claim
- If confidence is low, treat as inconclusive — add experiments rather than committing
- Always record the verdict and reasoning, regardless of outcome
- Multiple rounds of `partial` on the same claim → consider narrowing scope
- Do not describe missing comparisons as absent because the method performed poorly. Explain comparison gaps in methodological terms: the current study isolates a narrower scenario, answers a different deployment question, or reports a bounded diagnostic.
- If another method wins a reported metric, preserve the comparison in evidence artifacts and tables, but reframe the paper around the supported scenario-specific contribution rather than writing a broad self-defeating thesis.
- A scenario-scoped reframing is valid only when the scenario, pain point, and contribution are supported by local evidence or cited literature; it must not imply untested superiority.

## Integration

- Runs after `research-experiment-runner` completes experiments
- Reads from `experiment-audit` if available
- Routes to `ablation-planner` (if yes + ablations needed)
- Routes to `emnlp-paper-drafting` (if all claims confirmed)
- Routes back to `research-brief-to-experiment-plan` (if partial/no)
