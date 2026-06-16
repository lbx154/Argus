# Known Bugs

This file tracks known deviations that are still present in checked-in
artifacts but are intentionally preserved for provenance.

## Legacy benchmark summaries

`benchmarks/results/` remains ignored generated output, not source.
Checked-in evidence bundles live under `benchmarks/evidence/`. Keep
archival summaries in `benchmarks/reports/` or `docs/`; do not check
whole scratch result trees into this repository.

There are currently no checked-in exempt result bundles. If a bundle is
intentionally promoted from local scratch to source control, backfill the
protocol files or add `EXEMPT.md` and document the exception here in the
same change.
