# Results

This bundle archives the tracked TB2 run and its trial-level evidence.

## Aggregate

- Reward: 0.011236
- Wall minutes: 6.16
- Completed trials: 89
- Errored trials: 80
- Infra failure kind: docker_compose_failure
- Exception summary: none

## Caveats

- Token and cost totals are preserved only when present in the source result; otherwise the bundle records an explicit missing-cause field.
- Trial raw artifacts live under `jobs/raw/` and include `trial.log`, `agent/`, and `verifier/` transcripts.

## Source Result Keys

```json
[
  "finished_at",
  "id",
  "n_total_trials",
  "started_at",
  "stats",
  "updated_at"
]
```
