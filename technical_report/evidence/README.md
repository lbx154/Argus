# Reproducibility Materials

This directory contains the machine-readable data used to regenerate the tables
and empirical figures in the Argus technical report.

## Contents

- `website_results.json` contains the public result records summarized in the
  benchmark table, together with their source URLs.
- `paper_inventory.json` contains the de-duplicated public research portfolio used
  for the breadth summary.
- `swebench_pro/` contains the unified 731-task experiment summary, longitudinal
  Wave aggregates, and Reviewer-intervention statistics.
- `erdos_trace/` contains the public mathematical trajectory and aggregate
  role-efficiency measurements used in the vertical case study.
- `process_theory/` contains the numerical substitutions and theory-to-measurement
  mapping used by the process-to-capability analysis.

The report build uses only the fields required by the published tables and
figures. Credentials, private model reasoning, and raw runtime event streams are
not included. Source-specific regeneration instructions are provided in each
subdirectory.

Run `make all` from `technical_report/` to rebuild the paper and its generated
figures.
