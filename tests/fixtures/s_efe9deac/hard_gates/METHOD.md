# Method card — Budgeted group verification correction

## Status and claim

The operator locked this direction on 2026-10-26 and authorized implementation and experiments. This card specifies the first method to test; readiness does not establish learning benefit or superiority. Originality is a known risk, not a reason to reopen selection. Implementation must follow this card. A material change to the scientific claim requires an explicit Planner repair; development tuning cannot silently change it.

**Working claim.** On natural code-generation tasks, allocating a fixed budget of stricter execution checks across groups first, then expanding checks within groups where an accepted error was found, can improve held-out complete-program success and learning per verification cost over equally budgeted uniform checking and a strong CARE adaptation. The proposed reason is task-level heterogeneity in the stricter checker’s rejection of base-accepted programs: one observed rejection can make further checks in that group more useful. This is a hypothesis, not an established new mechanism or a superiority claim.

This claim concerns end-to-end learning, not partial-test coverage, a collection of complementary programs, verifier accuracy alone, or static gradient agreement. Group heterogeneity does not require dependence between independently generated responses conditional on the same task. No unbiased-gradient guarantee is claimed for adaptive partial correction, normalized advantages or clipped policy updates.

## Predictions, falsifiers and evaluation

Predict that naturally observed rejection in an audited group improves the yield of further checks there beyond untargeted checks, after accounting for task difficulty and base acceptance. That diagnostic alone does not establish learning benefit. Static shared-rollout comparisons can identify allocation and corrected-update differences; each end-to-end arm must generate its own subsequent rollouts.

The primary learning outcome is pass@1 of complete generated programs on independent held-out tasks under the fixed valid base-plus-extra union, with a common inference budget and reported uncertainty. Report learning per strict-query cost and actual total compute/time as well as final success. Training reward, one favorable checkpoint, audited-error yield and partial-test success cannot substitute for the primary outcome. Experiment must fix the concrete splits, primary aggregation, configurations and uncertainty analysis before confirmation testing.

The claim is challenged if the grouping signal has little usable mass, uniform coverage explains all gain, extra checking/compute explains the result, or budget-matched CARE/uniform checking reaches the same or better held-out success. First diagnose implementation, scoring, contracts, budget leakage and controls within the locked direction; an ordinary zero-difference pilot does not authorize switching topics. Only clear experimental refutation after those repairs can invoke the operator’s direction-change condition. Originality risk alone cannot trigger another search.

