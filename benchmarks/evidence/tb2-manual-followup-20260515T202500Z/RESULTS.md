# Results

This bundle demonstrates the failure mode, the fix, and an annotated manual-follow-up record on the same task family.

Failure side:
- `benchmarks/prompt_only_tb2/runs/no-review-smoke-20260513T150423Z/20260513T150423Z-o002-argus-cancel-async-tasks/`
- `argus_no_reviewer=1` and no explicit benchmark verifier gate were recorded.
- This is the self-satisfaction shortcut: the agent can finish without an independent verifier gate.

Fix side:
- `benchmarks/prompt_only_tb2/runs/20260515T201700Z-o002-argus-cancel-async-tasks/`
- `argus_benchmark_verifier_gate=1`, `zero_touch_success=True`, `human_interactions_after_assignment=0`.
- This is the corrected verifier-gated argus condition.

Manual-follow-up annotation:
- `benchmarks/prompt_only_tb2/runs/no-review-smoke-20260513T150423Z/20260513T150423Z-o002-argus-cancel-async-tasks/`
- `manual_commands=1`, `human_interactions_after_assignment=2`, `active_touch_minutes_after_assignment=6.0`.
- `manual_rescue=failed`, so this row records a rescue attempt that did not recover the task and therefore does not count as rescued.
- This is a bundle-local annotation row used to preserve the manual-attention schema in archival form.
- The bundle also includes `logs/results.csv` and `logs/verification_manual_followup_summary.csv` so the CSV exports carry the same manual-attention fields as `summary.tsv`.

Interpretation:
- The task outcome alone is not enough evidence of correctness for the protocol.
- The bundle shows why the benchmark runner must carry an explicit verifier gate instead of relying on the legacy reviewer-off shortcut.
- The annotated follow-up row shows how manual review outcomes should be recorded without treating every non-empty rescue note as a successful rescue.
