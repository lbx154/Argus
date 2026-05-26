# Evaluation Draft

## Main Tables

- TB2 comparison table:
  - generated artifact: `paper/artifacts/tb2_comparison.tsv`
  - generator: `paper/build_tb2_evaluation_artifacts.py`
  - reward
  - wall minutes
  - completed trials
  - errored trials
  - infrastructure failure kind
  - missing-cause annotations for token and cost totals
- User-study table:
  - generated artifact: `paper/artifacts/user_study_metrics.tsv`
  - generator: `paper/build_user_study_artifacts.py`
  - zero-touch success
  - human interactions after assignment
  - active touch minutes after assignment
  - manual commands
  - manual rescue
  - intervention severity
- Verifier-gate contrast table:
  - reviewer-off self-satisfaction failure
  - verifier-gated repair
  - manual-follow-up annotation

## Grounding

- Use `benchmarks/reports/2026-05-15-tb2-export-archive.md` for the archive
  framing.
- Use `benchmarks/reports/2026-05-16-tb2-argus-v12-true-023000Z.md` for the
  newest detached `argus-v12-true` state and its residual Docker Hub failures.
- Use `paper/artifacts/slm_llm_human_hierarchy.tsv` for the SLM->LLM->HUMAN
  tier definitions and local evidence paths.
- The SLM row is grounded by the tracked raw run at
  `experiments/tb2-bare-gpt54-mini-20260515T212131Z/`; the LLM and HUMAN rows
  are grounded by the archived `benchmarks/evidence/` bundles.
- Use `paper/artifacts/tb2_comparison.tsv` for the checked-in TB2 comparison
  package derived from `benchmarks/evidence/tb2-argus-v12-redux-20260515T201322Z/`,
  `benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/`, and the newest
  detached `argus-v12-true` archive at
  `benchmarks/evidence/tb2-argus-v12-true-20260516T023000Z/`.
- Use `paper/artifacts/user_study_metrics.tsv` for the checked-in user-study
  package derived from `benchmarks/evidence/prompt-only-tb2-smoke-20260515T1435Z/`
  and `benchmarks/evidence/tb2-manual-followup-20260515T202500Z/`.
- Use `paper/artifacts/verifier_gate_contrast.tsv` for the generated
  reviewer-off versus verifier-gated contrast package.
- The detached `argus-v12-true` run at
  `experiments/tb2-argus-v12-true-20260516T023000Z/` is the newest detached
  state and is archived at
  `benchmarks/evidence/tb2-argus-v12-true-20260516T023000Z/`; it completed all
  89 trials but still carries 80 errored trials, a `docker_compose_failure`
  infra-failure kind, and Docker Hub pull-rate failures in the trial logs, so it
  is failure-tainted evidence rather than a clean success.
- The older `experiments/tb2-argus-v12-true-20260516T005644Z/` bundle remains
  the launcher preflight control-path note with a Docker Hub rate-limit message.
