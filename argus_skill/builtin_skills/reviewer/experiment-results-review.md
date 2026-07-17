---
name: Experiment Results Review
description: Review experiment results for scientific validity before writing the paper. Check statistical significance, ablation fairness, effect size meaningfulness, and whether results support the intended claims.
category: paper-review
version: 1
created_at: 2026-05-28T00:00:00+00:00
---

# Experiment Results Review

Review experiment results as a senior ML researcher would before allowing the team to write the paper. The goal is to catch misleading or unconvincing evidence before it gets baked into claims.

## Reviewer stance
- You are deciding whether these results are worth writing up, not whether the paper is well-written.
- Weak results honestly presented are better than strong results from flawed methodology.
- If the results wouldn't survive peer review scrutiny, say so now — not after the paper is written.

## When the method did NOT beat the baseline

A loss to the baseline is a decision point, **not an automatic kill**. Do NOT
immediately pivot to a different idea. Work the ladder in order:

1. **Reflect on WHY**, with specific evidence from the runs, and classify the cause:
   - **Fixable** — a config/implementation bug, under-tuned hyperparameters, too
     few steps/samples, a decoding or eval mismatch, or a missing ablation control.
   - **Baseline artifact** — the baseline is unfairly strong, or the comparison is
     not apples-to-apples (a fair re-run may change the verdict).
   - **Genuine null / limited effect** — the mechanism does not help (here), and
     no cheap change is likely to flip it.
2. **Decide**:
   - If the cause is **Fixable** or a **Baseline artifact** and the fix is
     concrete and fits the remaining operator-approved budget, recommend
     **ONE more targeted optimization / re-run pass** aimed at exactly that fix.
     Name the single change and the metric that must move.
   - If the cause is a **genuine null / limited effect** with no credible cheap
     fix, **do NOT pivot** — recommend proceeding to **write the paper on the
     current results** as an honest negative / limited-gain finding: report where
     the method helps and where it does not, keep all negative and failed runs,
     and frame the contribution as the diagnostic / negative result itself.
3. **Bound it**: at most ONE reflect→optimize pass per idea before this decision
   is final. A second unmoved result routes to write-up, **not** another retry —
   this is the guardrail against sunk-cost commit-bias.
4. Reserve a **full-direction pivot** only when the results support neither a win
   nor an honest negative-result paper (e.g. the run is broken or inconclusive,
   not a clean negative).

Record the reflection, the cause classification, and the chosen next step
(`optimize_once` / `write_up_current` / `pivot`) in `verdict` and
`claim_recommendations`.

## Six review dimensions

Score each 1–5. Score 3+ on all dimensions = pass.

1. **Statistical and evidential support**
   - Is uncertainty handled appropriately for the data-generating process and claim?
   - Are confidence intervals, repeated measurements, sensitivity analyses,
     formal guarantees, or other domain-appropriate support reported?
   - Are small samples scoped honestly rather than rejected by a universal count?

2. **Ablation fairness**
   - Does each ablation isolate exactly one variable?
   - Are comparisons apples-to-apples (same training data, same compute, same hyperparameters)?
   - Is "without component X" implemented as removing X (fair) or as not training X at all (unfair)?
   - Would a reviewer call any comparison misleading?

3. **Effect size and practical significance**
   - Is the observed effect, null, diagnostic pattern, or boundary meaningful for
     the stated research question?
   - Are there regimes where the contribution helps, fails, or changes interpretation?
   - Are null results (no improvement) honestly reported?

4. **Claim support**
   - Do the numbers actually support the intended paper claims?
   - Are there overclaims (claiming "significant improvement" for marginal gains)?
   - Are there underclaims (missing an interesting finding in the data)?
   - Is the headline result the strongest honest claim, or is it cherry-picked?

5. **Baseline competitiveness**
   - Did baselines actually run and produce reasonable numbers (not all zeros)?
   - Is there at least one baseline that is competitive (not trivially weak)?
   - Would a reviewer say "this baseline is too weak to be meaningful"?
   - Are published results from prior work included where available?

6. **Completeness**
   - Are all planned conditions/baselines/benchmarks represented in results?
   - Are there missing runs that would change the conclusions?
   - Are error cases and failure modes analyzed?
   - Is there a null-result benchmark that shows where the method doesn't work?

## Output format

Return JSON:
```json
{
  "score": 1-5,
  "pass": true/false,
  "dimension_scores": {
    "statistical_significance": 1-5,
    "ablation_fairness": 1-5,
    "effect_size": 1-5,
    "claim_support": 1-5,
    "baseline_competitiveness": 1-5,
    "completeness": 1-5
  },
  "issues": ["specific issue 1", "specific issue 2"],
  "verdict": "one sentence overall judgment",
  "claim_recommendations": [
    "Claim X is supported — keep",
    "Claim Y is overclaimed — soften to Z",
    "Finding W is interesting but not claimed — consider adding"
  ]
}
```

## Hard blockers (auto-fail regardless of score)
- No domain-appropriate uncertainty or evidential justification for the headline result
- Unfair ablation: comparing trained component vs untrained/random component
- All baselines at 0% or trivially broken
- Headline claim contradicts the actual numbers
- Missing a planned benchmark/condition with no explanation
- Reporting only the best cherry-picked metric while hiding others

## Infrastructure validity
Flag infrastructure only when it invalidates the comparison, measurement, or
claim. Do not reject a custom runtime, small model, CPU path, or unbatched
execution merely because a larger/faster setup was available; those choices may
be the research subject or a controlled design decision.
