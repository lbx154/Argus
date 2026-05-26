# User Study Draft

## What To Report

- `zero_touch_success`
- `human_interactions_after_assignment`
- `active_touch_minutes_after_assignment`
- `manual_commands`
- `manual_rescue`
- `intervention_severity`
- Generated package: `paper/artifacts/user_study_metrics.tsv`
- Generator: `paper/build_user_study_artifacts.py`

## Counting Rules

- Count only post-assignment human work.
- Use explicit zeros when nothing happened.
- Leave values blank only when the field was not measured.
- Treat `manual_rescue=failed` as a failed rescue, not as a successful one.

## Examples

- `paper/artifacts/user_study_metrics.tsv`
- `benchmarks/evidence/tb2-manual-followup-20260515T202500Z/RESULTS.md`
- `docs/USER_STUDY_PROTOCOL.md`
- `benchmarks/evidence/prompt-only-tb2-smoke-20260515T1435Z/jobs/index.tsv`
