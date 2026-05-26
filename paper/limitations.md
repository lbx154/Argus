# Limitations Draft

## What To Say

- The tracked TB2 archives still contain infrastructure-failure rows such as
  `docker_address_pool_exhaustion`.
- That means some evidence is `broken_current_evidence` and should not be
  presented as a clean end-to-end success story.
- The SLM->LLM->HUMAN ladder is now materialized as a checked-in hierarchy
  artifact, but it is still a framing device rather than a quantitative claim.
- The reviewer-off versus verifier-gated contrast is now materialized as a
  checked-in artifact package, but it still packages a historical shortcut and a
  repair path rather than a general success guarantee.
- The SLM leg is backed by a tracked raw experiment run, while the LLM and HUMAN
  legs are backed by archived bundles; do not merge them into one synthetic
  result.
- The latest detached `argus-v12-true` run at
  `experiments/tb2-argus-v12-true-20260516T023000Z/` completed 89 trials but
  still carries 80 errored trials and Docker Hub pull-rate failures, so it is
  failure-tainted evidence rather than a clean success claim.
- The older `005644Z` bundle remains the launcher preflight control-path note.

## Safe Wording

- Describe the archived bundles as evidence-preserving, not as proof that every
  task family completed cleanly.
- Separate verifier-gated success from raw detached launch health.

## Supporting Artifacts

- `paper/artifacts/slm_llm_human_hierarchy.tsv`
- `paper/artifacts/tb2_comparison.tsv`
- `paper/artifacts/verifier_gate_contrast.tsv`
- `benchmarks/evidence/tb2-argus-v12-redux-20260515T201322Z/summary.tsv`
- `benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/summary.tsv`
- `benchmarks/evidence/tb2-argus-v12-true-20260516T023000Z/summary.tsv`
- `benchmarks/reports/2026-05-16-tb2-argus-v12-true-023000Z.md`
- `EXPERIMENTS.md`
