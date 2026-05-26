# Plan

Archive a bounded TB2 prompt-only reproducer for the reviewer-off self-satisfaction failure and a manual-follow-up annotation row.

Scope:
- failure side: legacy `no-review-smoke-20260513T150423Z` argus run that used `ARGUS_SKILL_NO_REVIEWER=1`
- fix side: fresh `20260515T201700Z-o002-argus-cancel-async-tasks` argus run with `ARGUS_SKILL_BENCHMARK_VERIFIER_GATE=1`
- annotation side: a bundle-local manual follow-up record that captures a failed rescue outcome and the associated human-attention fields
- both runs use the same task family (`cancel-async-tasks`) so the delta is protocol, not task drift

Artifacts copied into the bundle:
- result.json, stdout.log, stderr.log, metadata.json, prompt.txt for each side
- jobs/index.tsv for bundle-local pointers
- summary.tsv for the report/validator path
