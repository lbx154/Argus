# Single-Agent Failure Draft

## Failure Mode

- The reviewer-off shortcut is the cleanest historical example of self-satisfied
  success without an independent gate.
- Keep this section explicit about the distinction between:
  - `historical_only` failure evidence
  - `current_evidence` verifier-gated repair
- The durable verifier-gate contrast package lives at
  `paper/artifacts/verifier_gate_contrast.tsv` and is the table the paper
  should cite for the failure/fix contrast.

## Supporting Artifacts

- `paper/artifacts/verifier_gate_contrast.tsv`
- `benchmarks/evidence/tb2-reviewer-gate-contrast-20260515T201700Z/summary.tsv`
- `benchmarks/evidence/tb2-manual-followup-20260515T202500Z/summary.tsv`
- `benchmarks/evidence/tb2-reviewer-gate-contrast-20260515T201700Z/RESULTS.md`
- `benchmarks/prompt_only_tb2/runs/no-review-smoke-20260513T150423Z/20260513T150423Z-o002-argus-cancel-async-tasks/result.json`

## Takeaway

- The report should say the protocol failed when the gate was absent and only
  became defensible once the verifier gate was explicit.
- The generated contrast package should be the canonical paper artifact; the
  prose report is supporting context, not the only source of truth.
