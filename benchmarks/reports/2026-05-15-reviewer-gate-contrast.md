# TB v2 reviewer-gate contrast

> **Purpose**: archive a bounded reproducer for the reviewer-off self-satisfaction failure and the verifier-gated correction using only repo-local artifact paths.

## Core contrast

Failure condition:

- [`benchmarks/prompt_only_tb2/runs/no-review-smoke-20260513T150423Z/20260513T150423Z-o002-argus-cancel-async-tasks/`](/home/argustest/argus-skill/benchmarks/prompt_only_tb2/runs/no-review-smoke-20260513T150423Z/20260513T150423Z-o002-argus-cancel-async-tasks/)
- That result records `argus_no_reviewer=1` and has no explicit benchmark verifier gate.
- This is the legacy shortcut where the engineer can self-satisfy without an independent gate.

Passing condition:

- [`benchmarks/prompt_only_tb2/runs/20260515T201700Z-o002-argus-cancel-async-tasks/`](/home/argustest/argus-skill/benchmarks/prompt_only_tb2/runs/20260515T201700Z-o002-argus-cancel-async-tasks/)
- That result records `argus_benchmark_verifier_gate=1`, `zero_touch_success=True`, and `human_interactions_after_assignment=0`.
- This is the corrected verifier-gated argus condition.

Archived evidence bundle:

- [`benchmarks/evidence/tb2-reviewer-gate-contrast-20260515T201700Z/`](/home/argustest/argus-skill/benchmarks/evidence/tb2-reviewer-gate-contrast-20260515T201700Z/)

Manual-follow-up evidence bundle:

- [`benchmarks/evidence/tb2-manual-followup-20260515T202500Z/`](/home/argustest/argus-skill/benchmarks/evidence/tb2-manual-followup-20260515T202500Z/)
- The annotation row in that bundle records `manual_commands=1`, `human_interactions_after_assignment=2`, `active_touch_minutes_after_assignment=6.0`, and `manual_rescue=failed`.

## Why the structure matters

The failure mode is not “the task was hard.” It is “the protocol let the agent declare success without an independent verifier gate.” The corrected row shows the same task family under the current benchmark verifier gate, which makes the acceptance path explicit and measurable. That separation is why the reviewer-hallucination problem has to be handled in the runner and the archive, not inferred from a task result alone.

## Local artifacts

- Generated contrast table: [verifier_gate_contrast.tsv](/home/argustest/argus-skill/paper/artifacts/verifier_gate_contrast.tsv)
- Failure transcript: [stdout.log](/home/argustest/argus-skill/benchmarks/prompt_only_tb2/runs/no-review-smoke-20260513T150423Z/20260513T150423Z-o002-argus-cancel-async-tasks/stdout.log)
- Failure result: [result.json](/home/argustest/argus-skill/benchmarks/prompt_only_tb2/runs/no-review-smoke-20260513T150423Z/20260513T150423Z-o002-argus-cancel-async-tasks/result.json)
- Fix transcript: [stdout.log](/home/argustest/argus-skill/benchmarks/prompt_only_tb2/runs/20260515T201700Z-o002-argus-cancel-async-tasks/stdout.log)
- Fix result: [result.json](/home/argustest/argus-skill/benchmarks/prompt_only_tb2/runs/20260515T201700Z-o002-argus-cancel-async-tasks/result.json)
- Bundle summary: [summary.tsv](/home/argustest/argus-skill/benchmarks/evidence/tb2-reviewer-gate-contrast-20260515T201700Z/summary.tsv)
- Manual-follow-up protocol: [docs/USER_STUDY_PROTOCOL.md](/home/argustest/argus-skill/docs/USER_STUDY_PROTOCOL.md)
