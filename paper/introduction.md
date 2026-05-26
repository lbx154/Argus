# Introduction Draft

## Problem

- The repo has evidence for three layers of automation:
  - tracked raw SLM runs in `experiments/tb2-bare-gpt54-mini-20260515T212131Z/`
  - verifier-gated TB2 runs archived under `benchmarks/evidence/`
  - human follow-up and rescue annotations in the prompt-only smoke bundle
- The reviewer-off self-satisfaction contrast is now packaged as a checked-in
  artifact under `paper/artifacts/verifier_gate_contrast.tsv`.

## Claim Shape

- The repo now materializes the SLM->LLM->HUMAN ladder as a checked-in
  hierarchy artifact under `paper/artifacts/`.
- The defensible current claim is that the repo supports a progression from
  model-generated trial outputs to verifier-gated acceptance and then to
  human-attention annotations, with each tier tied to repo-local evidence.

## Evidence Anchors

- `paper/artifacts/slm_llm_human_hierarchy.tsv`
- `paper/artifacts/verifier_gate_contrast.tsv`
- `docs/USER_STUDY_PROTOCOL.md`
- `benchmarks/reports/2026-05-15-reviewer-gate-contrast.md`
- `benchmarks/reports/2026-05-15-tb2-export-archive.md`
- `EXPERIMENTS.md`
