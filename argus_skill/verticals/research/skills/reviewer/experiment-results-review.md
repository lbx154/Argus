---
name: "Experiment Results Review"
description: "Review experiment results for scientific validity before writing the paper. Check statistical significance, ablation fairness, effect size meaningfulness, and whether results support the intended claims."
---

# Experiment Results Review

Review whether the experiment was engineered correctly and whether its evidence
supports the selected idea's frozen premise. The goal is to catch invalid runs
and unsupported interpretations before they enter downstream claims.

## Reviewer stance
- You are validating an experiment, not repeating upstream idea selection.
- Weak results honestly presented are better than strong results from flawed methodology.
- A negative result for a selected idea is provisional. Start from the hypothesis
  that implementation, optimization, data, evaluator, scale, or experimental design
  may be at fault, and actively search for a scientifically justified route to a
  genuine positive result before declaring method failure.
- Treat method reasonableness, originality, and significance as frozen upstream
  decisions. Do not re-rank or re-litigate them from experimental outcomes.
- Check only (1) engineering and protocol validity and (2) whether the valid
  evidence supports, refutes, or leaves unresolved the frozen premise.
- If execution changed the method, premise, comparator, or claim boundary,
  report a fidelity failure and return it upstream; do not repair idea selection
  inside the experiment review.

## When the method did NOT beat the baseline

A loss is a root-cause and research-value decision point.

1. **Audit engineering adequacy.** Inspect source and executed artifacts, not the
   Engineer's confidence. Check reference parity, actual configuration,
   optimization/tuning, model/data capacity, evaluator semantics, fair budgets,
   dropped failures, and diagnostics tied to the proposed mechanism.
2. **Classify the cause:** misconfigured, under-engineered, unfair comparison,
   inconclusive due to insufficient discriminative power, genuine method
   failure, or infeasible under the available resources.
   Before claiming genuine failure, verify that the tasks exercised the proposed
   mechanism, the baseline had metric headroom, and the cases/repeats could
   resolve the predeclared contrast. A ceilinged/floored or underpowered tie is
   inconclusive, not a negative method result.
3. **Recommend the next experimental action by evidence and information gain:**
   - repair or optimize when a concrete credible change could give the idea a
     fairer test;
   - classify the frozen premise as supported, refuted, or inconclusive;
   - improve the implementation or method when a mechanism-based repair could
     recover the intended effect, then rerun the decisive comparison;
   - accept genuine failure only after reference parity, learning-signal activation,
     evaluator validity, adequate tuning/scale, and credible repairs have been checked
     by an independent Reviewer. Do not choose a replacement idea or decide publication
     value here.

There is no fixed number of optimization passes. Continue while credible fixes
with scientific rationale remain and the approved budget permits; stop only when
they are exhausted, contradicted, or no longer worth their information gain.
Preserve all valid evidence internally, but do not force every negative run into
the manuscript.

For an idea that has passed selection, a weak result triggers a positive-recovery
engineering loop while the mechanism remains plausible. Diagnose, improve, and rerun
until the method produces a genuine supported advantage or an independent Reviewer
certifies engineering adequacy and no credible repair remains within budget. Do not
send the project back to idea selection solely because the baseline won. Protocols,
metrics, seeds, slices, and baselines may change for documented methodological reasons;
no universal all-seed/all-baseline requirement applies.

## Six review dimensions

Score each 1–5. Score 3+ on all dimensions = pass. Here `pass` means the
experiment is engineering-valid and its interpretation is reviewable; it does
not mean the frozen premise was supported. Report that separately as
`idea_status`.

1. **Statistical and evidential support**
   - Is uncertainty handled appropriately for the data-generating process and claim?
   - Are confidence intervals, repeated measurements, sensitivity analyses,
     formal guarantees, or other domain-appropriate support reported?
   - Does every repeat have a named sampling-noise failure mode? Repeats detect
     sampling noise and nothing else. When the margin is far from the noise
     floor, one run suffices; near-noise margins retain the necessary repeats.
     Repeats cannot validate an implementation, repair a broken control, or find
     a claim-code mismatch. Require the cheaper faithfulness trace and positive
     control first, and flag generalized-fear reruns as ritual waste.
   - Are small samples scoped honestly rather than rejected by a universal count?
   - Is the reported margin wider than the spread of this run's own repeats? A
     win inside your own noise is not a small win; it is not a measurement.

2. **Ablation fairness**
   - Does each ablation isolate exactly one variable?
   - Are comparisons apples-to-apples (same training data, same compute, same hyperparameters)?
   - Is "without component X" implemented as removing X (fair) or as not training X at all (unfair)?
   - Would a reviewer call any comparison misleading?

3. **Effect size and practical significance**
   - Is the observed effect, null, diagnostic pattern, or boundary meaningful for
     the stated research question?
   - Was each numeric pass/fail cutoff justified by utility, risk, a domain
     standard, prior evidence, theory, or prospective sensitivity rather than an
     unsupported round-number target? If no justified cutoff exists, assess the
     continuous estimate, uncertainty, regimes, and cost-quality frontier; merely
     missing an arbitrary target cannot establish method failure.
   - Are there regimes where the contribution helps, fails, or changes interpretation?
   - Are claim-critical null results honestly represented without turning the
     paper into an exhaustive failure log?

4. **Claim support**
   - Do the numbers support the stated evidence conclusion about the frozen
     premise, whether that conclusion is supported, refuted, or inconclusive?
   - Are there overclaims (claiming "significant improvement" for marginal gains)?
   - Are there underclaims (missing an interesting finding in the data)?
   - Is the headline result useful and appropriately scoped?

5. **Baseline competitiveness**
   - Did baselines actually run and produce reasonable numbers (not all zeros)?
   - Is there at least one baseline that is competitive (not trivially weak)?
   - Did the baseline leave enough headroom for the comparison to distinguish
     methods, rather than saturating on an easy task slice?
   - Would a reviewer say "this baseline is too weak to be meaningful"?
   - For every baseline named after published work: was that method's own
     implementation, or its actual mechanism, run? Strip the labels and count
     families — nine rows that are five variants of one method have not
     compared this work to the field.
   - What does the literature report for *this* model on *this* benchmark? Write
     it beside your own number before judging anything built on it. A baseline
     far under its published score means the harness is what you measured, and
     every comparison resting on it is void — the losses included.
   - What fraction of generations hit their own token or step limit? A run cut
     off before it can answer scores like a method that cannot.
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
  "idea_status": "untested|inconclusive|supported|refuted",
  "dimension_scores": {
    "statistical_significance": 1-5,
    "ablation_fairness": 1-5,
    "effect_size": 1-5,
    "claim_support": 1-5,
    "baseline_competitiveness": 1-5,
    "completeness": 1-5
  },
  "issues": ["specific issue 1", "specific issue 2"],
  "verdict": "one sentence on engineering validity and evidence status",
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
- A baseline carrying a published method's name that is not that method: an
  approximation under the real name makes every comparison above it unreviewable
- A baseline far under the published score for the same model and benchmark with
  no stated reason: the harness is what was measured, so nothing above it can be
  reviewed until it is fixed
- A headline margin narrower than the spread of the run's own repeats, reported
  as a win
- A ceilinged/floored or underpowered tie is used to reject the method instead of
  being classified as inconclusive and redesigned
- Headline claim contradicts the actual numbers
- Missing a claim-critical planned benchmark/condition with no explanation
- Drafting unsupported superiority after a baseline loss; a diagnosed negative or
  boundary result should instead advance to constructive analysis and reframing
- Results are interpreted against a method, premise, comparator, or claim
  boundary that differs from the frozen selection

## Infrastructure validity
Flag infrastructure only when it invalidates the comparison, measurement, or
claim. A small or older model is acceptable when model scale is the research
subject or when it is explicitly labeled plumbing/compatibility evidence. Do not
accept it as headline evidence when the plan requires a current-generation
backbone and relevant current models are available.
