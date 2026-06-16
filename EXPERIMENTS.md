# Experiments index

A single-file index of every benchmark / ablation run on this machine.
Append a new row whenever you start a new experiment so future searches
can grep this file instead of trawling timestamps.

**Hard rule**: checked-in archive bundles live under
`benchmarks/evidence/<bench>-<scope>-<YYYY-MM-DD>[-vN]/`; ignored scratch
runs stay under `benchmarks/results/`
(see naming convention in any `PLAN.md`). **Never `/tmp`.**

## Index

| Date | Dir | Bench | Status | Headline |
|---|---|---|---|---|
| 2026-05-06 | `benchmarks/results/swebench_pro_smoke/`, `..._verifier_smoke/`, `..._verifier_v2/` | SWE-Bench-Pro smoke | done | adapter shakedown |
| 2026-05-06 | `benchmarks/results/swebpro-pilot-2026-05-06/` | SWE-Bench-Pro pilot | done | precursor to 05-07 pilot55 |
| 2026-05-07 | `benchmarks/results/swebpro-codex-baseline-2026-05-07/` | SWE-Bench-Pro 55-task pilot — codex-bare control | done | reward 0.164 (9/55), $140.20 |
| 2026-05-07 | `benchmarks/results/swebpro-argus-pilot2-2026-05-07/` | SWE-Bench-Pro 55-task pilot — argus-skill | done | reward 0.600 (33/55), $95.42 — see `benchmarks/reports/2026-05-07-pilot55.md` |
| 2026-05-09 | (was at `/tmp/ablation`) → `benchmarks/results/ablation-2026-05-09/` | toy "Build Typed Python Package" ablation | **mis-located** | 5 conditions × 1 round all pass; cost=$0.0 (telemetry bug); reviewer/gate fact-check inconsistent — needs rerun |
| 2026-05-09 | (was at `/tmp/ablation_p2`) → `benchmarks/results/ablation-2026-05-09-p2/` | toy "Typed Python Utility (ttlcache)" ablation | **mis-located** | 4 conditions × 1 round; pytest_cov + mypy gates inconsistent with reviewer "done" — reviewer hallucination evidence |

## Quick recall queries

```bash
# Find the most recent run by mtime
find /home/argustest/argus-skill/benchmarks/results -maxdepth 2 -type d -printf '%T@ %p\n' \
  | sort -nr | head -10

# What did 05-07 produce?
ls /home/argustest/argus-skill/benchmarks/results/swebpro-argus-pilot2-2026-05-07/

# All SWE-Bench-Pro runs ever
ls /home/argustest/argus-skill/benchmarks/results/ | grep ^swebpro-
```
