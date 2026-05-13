# Known Bugs

This file tracks known deviations that are still present in checked-in
artifacts but are intentionally preserved for provenance.

## Legacy benchmark summaries

Any top-level directory under `benchmarks/results/` that contains
`EXEMPT.md` is intentionally preserved as provenance-only history and is
skipped by `python -m benchmarks.validate_results benchmarks/results`.

Current exempt bundles:

- `benchmarks/results/ablation-2026-05-09/`
- `benchmarks/results/ablation-2026-05-09-p2/`
- `benchmarks/results/argus-skill-harbor/`
- `benchmarks/results/swebench_pro_smoke/`
- `benchmarks/results/swebench_pro_verifier_smoke/`
- `benchmarks/results/swebench_pro_verifier_v2/`
- `benchmarks/results/swebpro-argus-pilot2-2026-05-07/`
- `benchmarks/results/swebpro-codex-baseline-2026-05-07/`
- `benchmarks/results/swebpro-codex-baseline-2026-05-07-aborted-no-reviewer-bug/`
- `benchmarks/results/swebpro-codex-baseline-2026-05-07-aborted-old-code/`
- `benchmarks/results/swebpro-codex-baseline-2026-05-07-aborted-with-skill/`
- `benchmarks/results/swebpro-pilot-2026-05-06/`
- `benchmarks/results/tb2-ablation-2026-05-10/`
- `benchmarks/results/tb2-ablation-2026-05-10-v2/`
- `benchmarks/results/tb2-ablation-2026-05-10-v3/`
- `benchmarks/results/tb2-ablation-2026-05-10-v4-pri2/`
- `benchmarks/results/tb2-ablation-2026-05-10-v4-proto/`
- `benchmarks/results/tb2-bare-large-2026-05-01/`
- `benchmarks/results/tb2-bare-mini-2026-05-02/`
- `benchmarks/results/tb2-fullbench-2026-05-06-v12/`
- `benchmarks/results/tb2-fullbench-2026-05-22-v12-redux/`
- `benchmarks/results/tb2-fullbench-2026-05-22-v12-true/`
- `benchmarks/results/tb2-microbench-2026-05-04/`
- `benchmarks/results/tb2-microbench-2026-05-06-v10/`
- `benchmarks/results/tb2-microbench-2026-05-06-v11/`
- `benchmarks/results/tb2-microbench-2026-05-06-v8/`
- `benchmarks/results/tb2-microbench-2026-05-06-v9/`
- `benchmarks/results/tb2-smoke-2026-05-06-merged/`
- `benchmarks/results/tb2-stream-smoke-2026-05-06/`

If a new archival bundle is added, either backfill the protocol files or
add `EXEMPT.md` and extend this list in the same change.
