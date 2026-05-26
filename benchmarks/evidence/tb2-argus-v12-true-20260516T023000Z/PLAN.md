# Plan

Archive a tracked TB2 run into a repo-visible evidence bundle.

## Source

- Source run root: /home/argustest/argus-skill/experiments/tb2-argus-v12-true-20260516T023000Z
- Run id: tb2-argus-v12-true-20260516T023000Z
- Bundle type: tb2_fullbench_export
- Condition: argus-v12-true
- Dataset id: terminal-bench@2.0
- Dataset commit: 69671fbaac6d67a7ef0dfec016cc38a64ef7a77c

## Scope

- Preserve the aggregate Harbor run result.
- Preserve every trial directory under `jobs/raw/`.
- Copy root `stdout.log` and `stderr.log` into `logs/`.
- Record trial, verifier, and artifact paths in `summary.tsv` and `jobs/index.tsv`.
