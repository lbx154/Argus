# Plan

Archive a tracked TB2 run into a repo-visible evidence bundle.

## Source

- Source run root: /home/argustest/argus-skill/experiments/tb2-harbor-smoke-20260515T235537Z
- Run id: tb2-harbor-smoke-20260515T235537Z
- Bundle type: tb2_fullbench_export
- Condition: argus-v12-true-smoke
- Dataset id: 
- Dataset commit: 

## Scope

- Preserve the aggregate Harbor run result.
- Preserve every trial directory under `jobs/raw/`.
- Copy root `stdout.log` and `stderr.log` into `logs/`.
- Record trial, verifier, and artifact paths in `summary.tsv` and `jobs/index.tsv`.
