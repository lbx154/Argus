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
- `paper_case_study/` contains the public trajectory aggregates for six autonomous
  paper-production campaigns.
- `fla_kernel_optimization/` contains the certified GPU-kernel-optimization results
  (the `chunk_kda` op of `flash-linear-attention` on an NVIDIA B200) produced
  autonomously by the `kernel_engineering` vertical, together with the combined source
  diff against the frozen baseline. Submitted upstream as fla-org#1054 and **not yet
  accepted**; every number is measured at one shape on one GPU generation, which a
  maintainer has questioned. See that directory's *Upstream status* before citing it.

The report build uses only the fields required by the published tables and
figures. Credentials, private model reasoning, and raw runtime event streams are
not included. Source-specific regeneration instructions are provided in each
subdirectory.

Run `make all` from `technical_report/` to rebuild the paper and its generated
figures.
