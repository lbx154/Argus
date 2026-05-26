# Results

This bundle demonstrates the failure mode and the fix on the same task family.

Failure side:
- `benchmarks/prompt_only_tb2/runs/no-review-smoke-20260513T150423Z/20260513T150423Z-o002-argus-cancel-async-tasks/`
- `argus_no_reviewer=1` and no explicit benchmark verifier gate were recorded.
- This is the self-satisfaction shortcut: the agent can finish without an independent verifier gate.

Fix side:
- `benchmarks/prompt_only_tb2/runs/20260515T201700Z-o002-argus-cancel-async-tasks/`
- `argus_benchmark_verifier_gate=1`, `zero_touch_success=True`, `human_interactions_after_assignment=0`.
- This is the corrected verifier-gated argus condition.

Interpretation:
- The task outcome alone is not enough evidence of correctness for the protocol.
- The bundle shows why the benchmark runner must carry an explicit verifier gate instead of relying on the legacy reviewer-off shortcut.
