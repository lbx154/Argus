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

## Six review dimensions

Score each 1–5. Score 3+ on all dimensions = pass.

1. **Statistical significance**
   - Are improvements statistically significant (p < 0.05)?
   - Is there a significance test (McNemar, paired bootstrap, permutation test)?
   - Are confidence intervals or error bars reported?
   - With small test sets (<100), are results reliable or could they be noise?

2. **Ablation fairness**
   - Does each ablation isolate exactly one variable?
   - Are comparisons apples-to-apples (same training data, same compute, same hyperparameters)?
   - Is "without component X" implemented as removing X (fair) or as not training X at all (unfair)?
   - Would a reviewer call any comparison misleading?

3. **Effect size and practical significance**
   - Are the improvements large enough to matter in practice?
   - Is a 1% improvement on a 6% baseline meaningful, or is it noise?
   - Are there benchmarks where the method clearly helps AND benchmarks where it doesn't?
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
- No significance testing on the headline result
- Unfair ablation: comparing trained component vs untrained/random component
- All baselines at 0% or trivially broken
- Headline claim contradicts the actual numbers
- Missing a planned benchmark/condition with no explanation
- Reporting only the best cherry-picked metric while hiding others

## Infrastructure red flags in results
If results show signs of poor infrastructure choices, note them:
- Extremely slow training/eval times that suggest no framework was used
- Results from a custom scorer where a proper trained model was feasible
- Inference done one-example-at-a-time when batch was possible
- Model checkpoint is a tiny custom MLP when the compute budget allowed a real model
