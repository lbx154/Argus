# Experiments

This directory holds detached, tracked experiment bundles.

Contract:

- one bundle per run, named `experiments/<run_id>/`
- `manifest.json` records the exact command and metadata
- `pid` stores the launched process id
- `stdout.log` and `stderr.log` capture the run output
- `status.json` records launch/running/completed state

Use the wrappers in `benchmarks/run-fullbench-tb2.sh` and the condition
scripts in `benchmarks/run-fullbench-v12-*.sh` to start new runs.
