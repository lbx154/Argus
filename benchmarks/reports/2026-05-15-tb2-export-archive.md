# TB2 export archive

> **Purpose**: record the repo-local archival bundles produced from the tracked TB2 comparison runs.

## Exported bundles

- [`benchmarks/evidence/tb2-argus-v12-redux-20260515T201322Z/`](/home/argustest/argus-skill/benchmarks/evidence/tb2-argus-v12-redux-20260515T201322Z/)
- [`benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/`](/home/argustest/argus-skill/benchmarks/evidence/tb2-bare-gpt54-20260515T201322Z/)

## Contract

Each bundle includes `PLAN.md`, `BUILD_INFO.md`, `manifest.json`, `summary.tsv`, `RESULTS.md`, `logs/`, and `jobs/index.tsv`. The exported `summary.tsv` rows preserve the per-trial `reward` and `wall_minutes` values, plus explicit `*_missing_cause` fields for absent token and cost totals instead of silent nulls.

## Notes

- The argus run includes errored Harbor trials caused by Docker address-pool exhaustion; those rows now carry explicit `reward=0` and missing-cause annotations.
- The bare gpt-5.4 run exports the same archive contract for direct baseline comparison.
- Validation is repo-local: `python -m benchmarks.validate_results benchmarks/evidence`.
