# Figures and Tables Spec

## Table 1: Claim-to-Evidence Matrix

- Source: `paper/claims_to_evidence.tsv`
- Purpose: keep the paper honest about what is current evidence, what is
  historical contrast, and what still needs direct evidence.

## Table 2: TB2 Comparison Summary

- Source: `paper/artifacts/tb2_comparison.tsv`
  and `paper/build_tb2_evaluation_artifacts.py`
- Backing evidence:
  `benchmarks/evidence/tb2-argus-v12-redux-20260515T201322Z/summary.tsv`
  and `benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/summary.tsv`
- Fields: reward, wall minutes, status, infra failure kind, exception count,
  and explicit missing-cause annotations.

## Table 3: User-Study Annotation Schema

- Source: `paper/artifacts/user_study_metrics.tsv`
  and `paper/build_user_study_artifacts.py`
- Backing evidence:
  `docs/USER_STUDY_PROTOCOL.md`
  and `benchmarks/evidence/tb2-manual-followup-20260515T202500Z/summary.tsv`
- Fields: zero-touch success, human interactions after assignment, active touch
  minutes after assignment, manual commands, manual rescue, intervention
  severity.

## Table 4: SLM->LLM->HUMAN Hierarchy Package

- Source: `paper/artifacts/slm_llm_human_hierarchy.tsv`
  and `paper/build_hierarchy_artifacts.py`
- Backing evidence: the tracked raw `experiments/tb2-bare-gpt54-mini-20260515T212131Z/`
  run plus the archived bare-gpt54 and manual-followup bundles.
- Fields: tier label, source bundle, and raw local evidence paths.

## Table 5: Verifier Gate Contrast Package

- Source: `paper/artifacts/verifier_gate_contrast.tsv`
  and `paper/build_verifier_gate_artifacts.py`
- Backing evidence: the archived reviewer-gate contrast bundle plus the
  archived manual-followup bundle.
- Fields: reviewer-off failure, verifier-gated repair, manual-follow-up
  annotation, source bundle, and raw local evidence paths.

## Figure 1: Verifier Gate Contrast

- Source: `paper/artifacts/verifier_gate_contrast.tsv`
- Backing report: `benchmarks/reports/2026-05-15-reviewer-gate-contrast.md`
- Shows the failure-side reviewer-off shortcut and the verifier-gated fix-side
  contrast.
