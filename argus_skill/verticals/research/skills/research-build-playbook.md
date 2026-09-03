---
name: "Research Build Playbook"
description: "The single authoritative playbook for turning the selected Idea into a faithful, runnable method and baseline."
---

# Research Build Playbook

## Outcome

Produce a real implementation that tests the selected mechanism through the
actual entry point, together with its strongest fair baseline and evaluator.
Build the strongest faithful version of the idea, not the easiest version that
can pass a local check.

## Work

1. Read `HANDOFF.md` and trace every load-bearing thesis element to concrete
   code, configuration, data, outputs, and information boundaries.
2. Inspect the strongest relevant official implementations. Clone and run a
   fixed public revision when compiling, adapting, or comparing its code; reuse
   maintained components instead of reimplementing them from a paper summary.
3. Implement the method and baseline through real entry points under comparable
   data, compute, information, and evaluator access.
4. Run only the smallest engineering checks needed to establish imports,
   shapes, branches, numerical behavior, and end-to-end wiring. Do not build a
   ladder of toy scientific experiments.
5. Run a known detectable positive control through the same evaluator path.
6. Have a fresh Reviewer trace the selected thesis through the executed call
   chain and return `ALIGNED`, `MISMATCH`, or `NOT_IMPLEMENTED`.

Small Build benchmarks may decide whether an implementation strategy is viable.
They are not paper evidence and cannot test or reselect the Idea.

Validate real external, numerical, persistence, and security boundaries. Inside
the controlled implementation, trust established invariants. Do not add
redundant guards, fallback chains, reports, wrappers, or abstractions merely to
make the project look robust.

## Completion

The method, baseline, evaluator, positive control, run configuration, and
claim-critical code path are runnable and aligned. Known implementation defects
are repaired in Build. Manager alone advances the stage.

## Handoff

Replace project-root `HANDOFF.md` with `# HANDOFF — BUILD`. Keep only the
implemented mechanism, entry points, baseline, evaluator, positive-control
result, run configuration, resource needs, and unresolved Experiment risks.

## Progressive disclosure

Start with this Playbook. Open one specialist Skill only when its condition is
present, then return here. Do not preload the table.

| When needed | Open | Use it for |
|---|---|---|
| The thesis may have drifted from code | `engineer/hypothesis-implementation-contract.md` | Map the selected mechanism to the executed path |
| A fresh Reviewer must verify execution fidelity | `reviewer/claim-to-code-trace.md` | Trace claim-critical calls and formulas |
| Training or large inference infrastructure is required | `engineer/training-infrastructure-guide.md` | Select and reuse maintained frameworks |
| A concrete dependency or resource may block execution | `engineer/environment-readiness-gate.md` | Check only the resources this implementation uses |
| A surprising result may come from configuration | `engineer/suspect-the-setup.md` | Diagnose the highest-impact setup cause |
| Build readiness needs independent judgment | `reviewer/experiment-plan-review.md` | Check method, baseline, evaluator, and positive control |

Specialist Skills solve implementation questions. They do not add contracts,
reports, stages, or completion gates.
