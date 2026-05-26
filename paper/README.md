# Paper Workspace

This directory is a draft workspace for a claim-to-evidence paper.

## Layout

- `claims_to_evidence.tsv`: the source-of-truth claim matrix.
- `abstract.md`: abstract draft.
- `introduction.md`: introduction draft.
- `method.md`: method draft.
- `evaluation.md`: evaluation draft.
- `user-study.md`: user-study draft.
- `single-agent-failure.md`: failure-mode draft.
- `limitations.md`: limitations draft.
- `figures_tables.md`: figure and table spec.
- `artifacts/`: checked-in generated artifacts derived from repo-local
  evidence, including the SLM->LLM->HUMAN hierarchy package and the
  verifier-gate contrast package.

## Generator

- `python paper/build_hierarchy_artifacts.py`
- Regenerates the hierarchy package under `paper/artifacts/` from the checked-in
  TB2 bundles plus the tracked raw SLM experiment run.
- `python paper/build_verifier_gate_artifacts.py`
- Regenerates the verifier-gate contrast package under `paper/artifacts/` from
  the archived reviewer-gate and manual-followup bundles.

## Status Tags

- `current_evidence`: the repo-local artifacts directly support the claim.
- `historical_only`: the claim is grounded only as a historical contrast.
- `broken_current_evidence`: the repo has a live or archived failure that must be
  described as broken evidence, not as a current result.
- `TODO_evidence`: the narrative idea is useful, but the repo does not yet have a
  direct source for the exact phrasing or metric.

## Rules

- Keep every factual claim tied to a repo-local path.
- Do not upgrade `historical_only`, `broken_current_evidence`, or `TODO_evidence`
  into a confident result without a direct artifact to back it.
- Prefer citing `benchmarks/evidence/`, `benchmarks/reports/`, and `experiments/`
  over paraphrasing from memory.
- Treat `paper/artifacts/` as generated output that must still resolve to
  checked-in evidence paths.
