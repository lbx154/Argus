---
name: "Experiment Results Review"
description: "Review experiment results for scientific validity before writing the paper. Check statistical significance, ablation fairness, effect size meaningfulness, and whether results support the intended claims."
---

# Experiment Results Review

Review experiment results as a senior ML researcher would before allowing the team to write the paper. The goal is to catch misleading or unconvincing evidence before it gets baked into claims.

## Reviewer stance
- You are deciding whether these results are worth writing up, not whether the paper is well-written.
- Weak results honestly presented are better than strong results from flawed methodology.
- If the results wouldn't survive peer review scrutiny, say so now — not after the paper is written.

## When the method did NOT beat the baseline

A loss is a root-cause and research-value decision point.

1. **Audit engineering adequacy.** Inspect source and executed artifacts, not the
   Engineer's confidence. Check reference parity, actual configuration,
   optimization/tuning, model/data capacity, evaluator semantics, fair budgets,
   dropped failures, and diagnostics tied to the proposed mechanism.
2. **Classify the cause:** misconfigured, under-engineered, unfair comparison,
   genuine method failure, or infeasible under the available resources.
3. **Choose the next action by evidence and information gain:**
   - repair or optimize when a concrete credible change could give the idea a
     fairer test;
   - pivot when the original thesis is unsupported and a stronger direction is
     available;
   - recommend publication only when the negative/boundary result supports a
     surprising, robust, independently valuable thesis beyond "the method failed."

There is no fixed number of optimization passes. Stop when credible fixes are
exhausted or no longer worth their cost, not because a retry counter fired.
Preserve all valid evidence internally, but do not force every negative run into
the manuscript.

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
   - Are claim-critical null results honestly represented without turning the
     paper into an exhaustive failure log?

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
   - Are error cases and failure modes analyzed where they explain the thesis?

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
