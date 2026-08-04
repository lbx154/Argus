---
name: "Report-Only Head-to-Head Benchmark Evidence"
description: "Create a report-only benchmark comparison from existing accepted artifacts, proving whether a candidate beats a baseline under the same official local protocol without running new measurements or modifying benchmark inputs."
---

# Report-Only Head-to-Head Benchmark Evidence
## Description
Create a report-only benchmark comparison from existing accepted artifacts, proving whether a candidate beats a baseline under the same official local protocol without running new measurements or modifying benchmark inputs.

## Category
benchmark-reporting

## When to use
- The current stage is `report` or equivalent.
- Baseline and candidate measurements already exist.
- The task asks for JSON and Markdown evidence files.
- The Engineer must compare latency, speedup, correctness, metadata, commands, and provenance from existing artifacts only.

## When NOT to use
- New benchmark runs, GPU measurements, optimization, harness edits, or candidate edits are required.
- The accepted candidate or baseline artifact paths are unknown and cannot be inferred from state files.
- The task requires claiming external leaderboard, server-side, or vendor-reported scores not present in the provided artifacts.

## How to solve
1. Read the governing files first: `<agent_instructions>`, `<pipeline_state>`, `<checklists>`, `<best_record>`, `<optimize_log>`, and any baseline/candidate metadata, summary, or log paths referenced by `<best_record>`.
2. Confirm the active stage is report-only. Record forbidden actions and restrict edits to `<report_json>` and `<report_markdown>`.
3. Extract the target path, accepted candidate path, baseline run path, candidate run path, exact baseline command, exact candidate command, metric name, baseline metric value, candidate metric value, correctness counts, run records, and artifact paths.
4. Run only explicitly allowed CPU-only validators, for example `<python> <metadata_validator> <baseline_metadata>`, `<python> <metadata_validator> <candidate_metadata>`, and `<git_status_command>`. Capture concise stdout/stderr and exit status.
5. Inspect metadata for protocol evidence: GPU lease or global lock, GPU device identity such as GPU0, clock evidence, checksum evidence, wrapper/protocol identifiers, timestamps, and command provenance.
6. Compute comparison fields from the supplied measurements: `<speedup> = <baseline_metric> / <candidate_metric>` for lower-is-better latency, and `<delta> = <baseline_metric> - <candidate_metric>`. Preserve full precision where provided.
7. Write `<report_json>` as machine-readable evidence. Include baseline and candidate records, exact commands, metric values and units, speedup, delta, `candidate_beats_baseline`, correctness counts, validator results, metadata paths, summary paths, log paths, protocol evidence, current stage, and caveats.
8. Write `<report_markdown>` as a human-readable head-to-head report. State the exact measured metric, the baseline and candidate commands, the artifact inventory, validator command outputs, correctness evidence, per-run records, speedup/delta, and the local-protocol caveat.
9. Explicitly state that the result is local official-protocol latency evidence and does not claim external/server-side score percentages unless such data is directly present and requested.
10. Verify acceptance without touching forbidden files: confirm both report files exist, JSON parses, `candidate_beats_baseline=true` when supported by the metric, required commands and outputs are recorded, and no new GPU, harness, candidate, measurement, scorer, timing, correctness, or pipeline-state work occurred.

## Pitfalls
- Do not rerun benchmarks or trigger GPU work during a report-only stage.
- Do not edit candidates, harnesses, scorers, timing files, correctness files, benchmark logs, or pipeline state.
- Do not fabricate external scores, SOL%, leaderboard values, or server-side claims.
- Do not compare measurements from different protocols without calling that out.
- Do not round away material precision in latency, speedup, or delta fields.
- Do not omit exact commands; command provenance is part of the evidence.