# Method Draft

## Data Sources

- TB2 detached fullbench launches under `experiments/tb2-*`.
- Archived bundles under `benchmarks/evidence/tb2-*`.
- Prompt-only TB2 smoke bundle under `benchmarks/evidence/prompt-only-tb2-smoke-20260515T1435Z/`.
- Manual-follow-up contrast bundle under `benchmarks/evidence/tb2-manual-followup-20260515T202500Z/`.

## Normalization

- Use archive bundles as the canonical evidence surface.
- Keep `summary.tsv` rows explicit about missing causes for token and cost
  fields instead of leaving those values implicit.
- Preserve `manual_rescue=failed` as a non-success outcome.

## Protocol

- The user-study rubric lives in `docs/USER_STUDY_PROTOCOL.md`.
- The SLM->LLM->HUMAN hierarchy is materialized by
  `paper/build_hierarchy_artifacts.py`, which writes
  `paper/artifacts/slm_llm_human_hierarchy.tsv` from a tracked raw SLM run and
  the archived LLM/HUMAN TB2 bundles.
- The reviewer-off versus verifier-gated contrast is materialized by
  `paper/build_verifier_gate_artifacts.py`, which writes
  `paper/artifacts/verifier_gate_contrast.tsv` from the archived reviewer-gate
  and manual-followup bundles.
- The prompt-only summary/export chain should be read through the archived
  bundle and the smoke `results.csv`/`verification_*_summary.csv` exports.

## Evidence Anchors

- `paper/artifacts/slm_llm_human_hierarchy.tsv`
- `paper/artifacts/verifier_gate_contrast.tsv`
- `experiments/tb2-bare-gpt54-mini-20260515T212131Z/manifest.json`
- `benchmarks/evidence/tb2-manual-followup-20260515T202500Z/summary.tsv`
- `benchmarks/evidence/tb2-argus-v12-redux-20260515T201322Z/summary.tsv`
- `benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/summary.tsv`
