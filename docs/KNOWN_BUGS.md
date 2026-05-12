# Known Bugs

This file tracks known deviations that are still present in checked-in
artifacts but are intentionally preserved for provenance.

## Legacy benchmark summaries

- `benchmarks/results/tb2-ablation-2026-05-10-v3/` is a legacy run that
  predates the current `summary.tsv` schema. It is marked exempt via
  `EXEMPT.md` rather than backfilled with fabricated token columns.
- `benchmarks/results/tb2-ablation-2026-05-10-v4-pri2/` is also marked
  exempt for the same reason.

The validation gate is `python -m benchmarks.validate_results benchmarks/results`.
