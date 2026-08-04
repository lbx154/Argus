---
name: "Digital Circuit Benchmark Execution"
description: "Run fixed-harness RTL benchmarks with isolated workspaces, immutable first-attempt evidence, bounded oracle-guided repair, and reproducible official scoring."
---

# Digital Circuit Benchmark Execution

## Operating method

1. Freeze the dataset revision/source, selected task IDs, selection rule, prompt/context, evaluator version, simulator image, score definition, model/backend, budget, and repair policy before seeing any result.
2. Extract only allowed public prompt/context into a unique task workspace. Never read the golden patch/output, hidden harness source, other task solutions, or prior answers.
3. Before generation, audit every prompt reference to a pre-existing specification, RTL, testbench, or document. Each referenced file must exist in the frozen public context. If it is absent, stop with a benchmark-packaging defect; never guess the missing contract or learn it incrementally from official failures.
4. Generate non-empty synthesizable RTL and task-local independent tests without changing visible inputs or scorer files.
5. Prefer the shortest auditable path for a functional benchmark. Specification, RTL, and verification remain mandatory; synthesis/PPA may be explicitly out of scorer scope when no implementation metric is claimed.
6. Discover tools in order: project-native command, host `PATH`, declared project environment, then declared already-local container. Record human-readable versions and image identities, and never pull tools from the network during a frozen run.
7. Serialize official scoring and any shared container runtime with the campaign lock. Use a fresh output prefix for every attempt.
8. Record the first official attempt immutably before any repair. Keep Pass@1 separate from post-repair success.
9. On failure, expose only the allowed official failure/oracle log to a narrow repair mission. Do not expose hidden source or infer golden implementation details.
   Before dispatch, the trusted controller must run
   `python -m argus_skill.verticals.digital_circuit.benchmark.stages
   --project-root . --prepare-repair-expectation --generation N --iteration N
   --mission-id ID --answer-path <public-output>` for every answer artifact.
   Never let the repair worker invent or rewrite this pre-dispatch identity.
10. Record evaluator infrastructure/no-execution separately from compile,
    simulation, or RTL mismatch. A scorer that did not execute yields no new RTL
    correctness conclusion.
11. If the safe official failure signature is unchanged, do not regenerate the
    same idea: record a different public-contract-derived hypothesis and a changed
    task-local test. Never copy mismatch vectors or inferred hidden behavior into
    either artifact.
12. Bound repair by the predeclared attempt, cost, or time limit. Preserve every failed attempt and stop honestly when the cap is reached.
13. Report per-task status plus aggregate Pass@1, post-repair success, category/difficulty macro averages, compile/simulation failures, evaluator-infrastructure failures, cost, elapsed time, and failure taxonomy.

## Pre-score handoff schema

The controller-facing gate is `evidence/preflight.json`. It must contain:

```json
{
  "status": "pass",
  "top_modules": ["exact_public_top"],
  "rtl_files": ["rtl/generated.sv"],
  "output_paths": ["rtl/expected_output.sv"],
  "compile_results": [{"returncode": 0}]
}
```

For compatibility, public manifests and preflight accept either singular
`"output_path"` or plural `"output_paths"`; when both appear they must normalize
to the same ordered paths. Controllers use this same normalization.

Use `"status": "blocked"` when interface closure or elaboration is incomplete;
never invoke the official scorer from a blocked preflight.

## Required campaign artifacts

```text
selection.json                 # frozen task selection and dataset revision/source
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
