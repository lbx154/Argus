---
name: Digital Circuit Benchmark Execution
description: Run fixed-harness RTL benchmarks with isolated workspaces, immutable first-attempt evidence, bounded oracle-guided repair, and reproducible official scoring.
category: anti-cheat
version: 1
---

# Digital Circuit Benchmark Execution

## Operating method

1. Freeze the dataset revision/hash, selected task IDs, selection rule, prompt/context, evaluator version, simulator image, score definition, model/backend, budget, and repair policy before seeing any result.
2. Extract only allowed public prompt/context into a unique task workspace. Never read the golden patch/output, hidden harness source, other task solutions, or prior answers.
3. Generate non-empty synthesizable RTL and task-local independent tests without changing visible inputs or scorer files.
4. Prefer the shortest auditable path for a functional benchmark. Specification, RTL, and verification remain mandatory; synthesis/PPA may be explicitly out of scorer scope when no implementation metric is claimed.
5. Discover tools in order: project-native command, host `PATH`, declared project environment, then declared already-local container. Record versions/digests and never pull tools from the network during a frozen run.
6. Serialize official scoring and any shared container runtime with the campaign lock. Use a fresh output prefix for every attempt.
7. Record the first official attempt immutably before any repair. Keep Pass@1 separate from post-repair success.
8. On failure, expose only the allowed official failure/oracle log to a narrow repair mission. Do not expose hidden source or infer golden implementation details.
9. Bound repair by the predeclared attempt, cost, or time limit. Preserve every failed attempt and stop honestly when the cap is reached.
10. Report per-task status plus aggregate Pass@1, post-repair success, category/difficulty macro averages, compile/simulation failures, cost, elapsed time, and failure taxonomy.

## Required campaign artifacts

```text
selection.json                 # frozen task selection and dataset hash
controller.json                # active isolated workspaces and locks
results.jsonl                  # append-only official attempt ledger
<task>/prompt.json
<task>/MISSION.md
<task>/evidence/official-score-<attempt>/
<task>/evidence/official-score-history.jsonl
```

The append-only ledger must identify task, attempt, backend/model, pass/fail,
scorer configuration, hidden/golden exposure flags, non-empty patch evidence,
runtime, failure class, and evidence path.
