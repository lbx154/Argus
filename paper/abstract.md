# Abstract Draft

- Argus improves the TB2 workflow by making the verifier gate explicit and by
  preserving the evidence chain from detached launches into archival bundles.
- The paper should present the `verifier_gated_repair` row as the strongest
  current claim and the `reviewer_off_failure` row as historical contrast.
- The reviewer-off versus verifier-gated contrast now has a checked-in artifact
  package under `paper/artifacts/verifier_gate_contrast.tsv` instead of living
  only as a prose report.
- The SLM->LLM->HUMAN hierarchy still has its own checked-in artifact package
  under `paper/artifacts/` instead of living only as a narrative placeholder.
- The evaluation summary should reference the archived TB2 bundles, not only the
  scratch `experiments/` roots.
- The user-study angle belongs in the manual-attention schema described in
  `docs/USER_STUDY_PROTOCOL.md`.

Primary evidence:

- `experiments/tb2-bare-gpt54-mini-20260515T212131Z/manifest.json`
- `paper/artifacts/verifier_gate_contrast.tsv`
- `benchmarks/evidence/tb2-reviewer-gate-contrast-20260515T201700Z/RESULTS.md`
- `benchmarks/evidence/tb2-argus-v12-redux-20260515T201322Z/summary.tsv`
- `benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/summary.tsv`
