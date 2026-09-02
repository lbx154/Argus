---
name: "result-to-claim"
description: "After experiments complete, judge which claims results support, which they don't, and what evidence is missing. Route to implementation repair, supplementary evidence, or paper writing without reopening idea selection."
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
2. If the idea has already passed selection and the core mechanism remains
   plausible, treat the negative as a probable engineering/debugging signal and run a
   positive-recovery loop before drafting or reframing:
   diagnose implementation fidelity, optimization, evaluator semantics, data,
   budgets, scale, and mechanism-specific failures; make credible targeted
   improvements; then rerun the decisive comparison.
3. Continue while scientifically justified repairs remain and the approved budget
   permits. Require independent confirmation of reference parity, valid learning
   signal, fair evaluation, adequate tuning/scale, and implementation adequacy before
   accepting genuine method failure.
4. If the failure is implementation, dependency, infrastructure, evaluator, or
   claim-code mismatch, repair it inside the selected idea. It is not scientific
   evidence and never reopens idea discovery or selection.
5. Build the strongest truthful paper thesis the evidence supports: a characterized
   boundary, mechanism failure, scaling regime, benchmark lesson, or practical
   decision can be the contribution. Do not require the original method to win.
6. Advance to analysis/draft by default. Redesign the implementation or evidence
   plan within the selected idea when measurement is invalid or no useful conclusion
   remains after bounded reframing; do not launch a replacement-idea search.

#### `partial` — Claim partially supported
1. Update the working claim to reflect what IS supported.
2. Diagnose concrete method, implementation, optimization, data, scale, and evaluator
   weaknesses. Iteratively apply credible mechanism-based repairs and rerun the
   decisive comparison while they retain information value.
3. Choose a concrete scenario/pain point only when it is independently motivated
   by the original question or literature, not mined after seeing the results.
4. Record the comparison boundary: absent baselines, stronger baselines, missing
   ablations, or non-decisive metrics.
5. Run the repair or supplementary experiments, then re-run result-to-claim.

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
- Multiple rounds of `partial` on the same claim → crystallize the supported
  boundary and advance to the paper rather than looping
- Keep the complete evidence record internal. The manuscript is a claim-driven
  selection of the strongest valid evidence needed to establish and explain its
  thesis, not a chronological experiment report.
- Do not describe missing comparisons as absent because the method performed poorly. Explain comparison gaps in methodological terms: the current study isolates a narrower scenario, answers a different deployment question, or reports a bounded diagnostic.
- If another method wins a reported metric, preserve the comparison internally
  and disclose it when it bears on the thesis. Improve the selected method when a
  credible repair exists; otherwise reframe only around a genuinely supported,
  independently motivated contribution rather than a self-defeating chronology.
- A scenario-scoped reframing is valid only when the scenario, pain point, and contribution are supported by local evidence or cited literature; it must not imply untested superiority.
- Judge a successful recovery with evidence proportional to its actual claim,
  variance, field norms, and budget. It need not win on every seed, benchmark, or
  strongest baseline. Evaluators, slices, repeats, and comparisons may evolve for
  documented methodological reasons when previous outcomes remain visible and the
  final claim is scoped to what was tested.

## Integration

- Runs after `research-experiment-runner` completes experiments
- Reads from `experiment-audit` if available
- Routes to `ablation-planner` (if yes + ablations needed)
- Routes to the active venue's drafting skill (if all claims are confirmed)
- Routes back to `research-brief-to-experiment-plan` (if partial/no)
